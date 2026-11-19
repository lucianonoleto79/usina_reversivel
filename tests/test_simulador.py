"""Testes do simulador da usina reversível.

As séries de teste imitam o formato do ``serie.csv`` do curtailment_simulator.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from usina_reversivel import ParametrosUsina, ler_serie_curtailment, simular  # noqa: E402
from usina_reversivel.config import G, HM3, RHO  # noqa: E402


def serie(n=96, passo="15min", vert=None, corte_fv=None, termica=None, carga=None):
    idx = pd.date_range("2024-08-11", periods=n, freq=passo)
    z = np.zeros(n)
    return pd.DataFrame({
        "vertimento": z if vert is None else vert,
        "corte_fv": z if corte_fv is None else corte_fv,
        "corte_eolica": z, "excedente": z,
        "termica_flex": z if termica is None else termica,
        "deficit": z,
        "carga_residual": np.linspace(40000, 60000, n) if carga is None else carga,
    }, index=idx)


def dia_tipico(n=96):
    """Excesso no meio do dia (10h-15h) e térmica de rampa à noite (18h-21h)."""
    h = np.arange(n) / 4
    vert = np.where((h >= 10) & (h < 15), 800.0, 0.0)
    corte = np.where((h >= 11) & (h < 14), 400.0, 0.0)
    term = np.where((h >= 18) & (h < 21), 300.0, 0.0)
    return serie(n, vert=vert, corte_fv=corte, termica=term)


def params(**kw):
    base = dict(vazao_m3s=200, queda_m=300, pot_turbina_mw=530, pot_bomba_mw=670,
                vol_montante_hm3=50, vol_armazenamento_hm3=5)
    base.update(kw)
    return ParametrosUsina(**base)


def test_conversao_potencia():
    p = params()
    assert p.pot_hidraulica_mw == pytest.approx(RHO * G * 200 * 300 / 1e6)
    assert p.pot_turbina_ef_mw == pytest.approx(min(530, p.pot_hidraulica_mw * 0.90))
    assert p.pot_bomba_ef_mw == pytest.approx(min(670, p.pot_hidraulica_mw / 0.88))
    assert p.avisos() == []
    assert params(pot_turbina_mw=1000).avisos()  # incoerente com ρgQHη


def test_balanco_energia_e_agua_fecham():
    p = params(afluencia_m3s=50, defluencia_uhe_m3s=40)
    r = simular(dia_tipico(), p)
    en = r.balanco["energia"]
    assert en["bombeada_consumida_MWh"] > 0 and en["gerada_MWh"] > 0
    assert abs(en["fechamento_erro_MWh"]) < 1e-6
    assert en["rendimento_ciclo_realizado"] == pytest.approx(p.rend_ciclo)
    ag = r.balanco["agua_hm3"]
    nat = (50 - 40) * 3600 * 24 / HM3
    total_ini = ag["armazenamento_inicial"] + ag["montante_inicial"] + nat
    total_fim = (ag["armazenamento_final"] + ag["montante_final"] + ag["vertido_montante"]
                 - ag["defluencia_nao_atendida"])
    assert total_fim == pytest.approx(total_ini)


def test_limites_respeitados():
    p = params()
    r = simular(dia_tipico(), p)
    s = r.serie
    assert (s["bombeamento"] <= p.pot_bomba_ef_mw + 1e-9).all()
    assert (s["geracao"] <= p.pot_turbina_ef_mw + 1e-9).all()
    assert (s["bombeamento"] <= s["excesso_disponivel"] + 1e-9).all()
    assert ((s["bombeamento"] > 0) & (s["geracao"] > 0)).sum() == 0
    assert s["vol_armazenamento_hm3"].between(0, 5 + 1e-9).all()
    assert s["vol_montante_hm3"].between(0, 50 + 1e-9).all()


def test_sem_excesso_nao_bombeia():
    r = simular(serie(termica=np.full(96, 200.0)), params(nivel_ini_armazenamento=0.5))
    assert r.serie["bombeamento"].sum() == 0
    assert r.balanco["energia"]["gerada_MWh"] > 0


def test_armazenamento_cheio_nao_bombeia():
    r = simular(serie(vert=np.full(96, 1000.0)), params(nivel_ini_armazenamento=1.0))
    assert r.serie["bombeamento"].sum() == 0
    assert r.disponibilidade["curtailment_hidraulico"]["limitado_armazenamento_cheio_pct"] == 100


def test_montante_vazio_nao_bombeia():
    r = simular(serie(vert=np.full(96, 1000.0)), params(nivel_ini_montante=0.0))
    assert r.serie["bombeamento"].sum() == 0
    assert r.disponibilidade["curtailment_hidraulico"]["limitado_montante_vazio_pct"] == 100


def test_reserva_minima_preservada():
    p = params(nivel_ini_armazenamento=1.0, reserva_min=0.3, politica_geracao="ponta",
               percentil_ponta=0.0)
    r = simular(serie(), p)
    assert r.serie["nivel_armazenamento"].min() >= 0.3 - 1e-9


def test_fontes_bombeamento_filtra():
    s = dia_tipico()
    r = simular(s, params(fontes_bombeamento=("corte_fv",)))
    assert (r.serie["bombeamento"] <= s["corte_fv"].to_numpy() + 1e-9).all()


def test_disponibilidade_por_cenario():
    r = simular(dia_tipico(), params())
    d = r.disponibilidade
    assert d["curtailment_hidraulico"]["horas"] == pytest.approx(5.0)
    assert d["excesso_intermitente"]["horas"] == pytest.approx(3.0)
    assert d["periodo_total"]["horas"] == pytest.approx(24.0)
    assert 0 <= d["curtailment_hidraulico"]["disponibilidade_absorcao_pct"] <= 100


def test_leitura_serie_csv(tmp_path):
    pasta = tmp_path / "rodada"
    pasta.mkdir()
    s = dia_tipico()
    s.index.name = "instante"
    s.to_csv(pasta / "serie.csv")
    lida = ler_serie_curtailment(pasta)
    assert len(lida) == 96
    with pytest.raises(ValueError):
        (tmp_path / "ruim.csv").write_text("instante,x\n2024-01-01,1\n2024-01-02,2\n")
        ler_serie_curtailment(tmp_path / "ruim.csv")


def test_parametros_invalidos():
    with pytest.raises(ValueError):
        params(queda_m=0)
    with pytest.raises(ValueError):
        params(rend_bomba=1.5)

"""Simulação passo a passo da operação da usina reversível.

Convenções: potências em MW (médias do passo), energias em MWh, volumes em m³
internamente e em hm³ nas saídas. O "estoque" do reservatório de armazenamento é
expresso em energia gerável (MWh = volume × ρ·g·H·η_t).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import HM3, ParametrosUsina
from .entrada import passo_horas, preparar_serie

TOL = 1e-6
CENARIO_CURTAILMENT = "curtailment_hidraulico"
CENARIO_INTERMITENTE = "excesso_intermitente"
CENARIO_SEM = "sem_excesso"


@dataclass
class ResultadoUsina:
    serie: pd.DataFrame
    balanco: dict
    disponibilidade: dict
    params: ParametrosUsina
    dt_h: float


def mascaras_cenarios(entrada: pd.DataFrame) -> dict[str, np.ndarray]:
    """Passos de cada cenário (podem se sobrepor).

    * ``curtailment_hidraulico``: há vertimento turbinável (vertimento > 0);
    * ``excesso_intermitente``: há corte de eólica/FV ou excedente não resolvido;
    * ``sem_excesso``: nenhum excesso na rede.
    """
    vert = (entrada["vertimento"] > TOL).to_numpy()
    inter = ((entrada["corte_fv"] + entrada["corte_eolica"] + entrada["excedente"])
             > TOL).to_numpy()
    return {CENARIO_CURTAILMENT: vert, CENARIO_INTERMITENTE: inter,
            CENARIO_SEM: ~(vert | inter)}


def classificar_cenarios(entrada: pd.DataFrame) -> pd.Series:
    """Rótulo de cada passo: curtailment_hidraulico, excesso_intermitente,
    ambos ou sem_excesso."""
    m = mascaras_cenarios(entrada)
    v, i = m[CENARIO_CURTAILMENT], m[CENARIO_INTERMITENTE]
    cen = np.where(v & i, "ambos", np.where(v, CENARIO_CURTAILMENT,
                                            np.where(i, CENARIO_INTERMITENTE, CENARIO_SEM)))
    return pd.Series(cen, index=entrada.index, name="cenario")


def simular(entrada: pd.DataFrame, params: ParametrosUsina) -> ResultadoUsina:
    p = copy.deepcopy(params)
    e = preparar_serie(entrada)
    dt = passo_horas(e.index)
    n = len(e)

    pt, pb = p.pot_turbina_ef_mw, p.pot_bomba_ef_mw
    k_t = p.mwh_por_m3_turbinado          # MWh gerados por m³
    k_b = p.m3_por_mwh_bombeado           # m³ elevados por MWh consumido
    va_max = p.vol_armazenamento_hm3 * HM3
    vm_max = p.vol_montante_hm3 * HM3
    va_res = p.reserva_min * va_max
    q_nat = (p.afluencia_m3s - p.defluencia_uhe_m3s) * dt * 3600.0  # m³ por passo

    fontes = list(p.fontes_bombeamento)
    excesso = e[fontes].sum(axis=1).to_numpy(float)
    demanda_term = (e["termica_flex"] + e["deficit"]).to_numpy(float)
    limiar_ponta = float(np.quantile(e["carga_residual"], p.percentil_ponta))
    ponta = (e["carga_residual"] >= limiar_ponta).to_numpy() \
        if p.politica_geracao in ("ponta", "termica_ponta") else np.zeros(n, bool)
    usar_term = p.politica_geracao in ("termica", "termica_ponta")

    va = p.nivel_ini_armazenamento * va_max
    vm = p.nivel_ini_montante * vm_max
    va0, vm0 = va, vm

    cols = ("cap_bombeamento", "cap_geracao", "bombeamento", "geracao", "vol_bombeado",
            "vol_turbinado", "vertido_montante", "defluencia_nao_atendida",
            "vol_armazenamento", "vol_montante", "lim_armaz_cheio", "lim_montante_vazio",
            "lim_armaz_vazio", "termica_deslocada", "ger_ponta")
    o = {c: np.zeros(n) for c in cols}

    for t in range(n):
        # 1) balanço natural do montante (afluência − defluência da UHE)
        vm += q_nat
        if vm > vm_max:
            o["vertido_montante"][t] += vm - vm_max
            vm = vm_max
        elif vm < 0:
            o["defluencia_nao_atendida"][t] = -vm
            vm = 0.0

        # 2) disponibilidade no início do passo
        lim_espaco = (va_max - va) / k_b / dt        # MW que cabem no armazenamento
        lim_agua = vm / k_b / dt                     # MW limitados pela água do montante
        cap_b = max(0.0, min(pb, lim_espaco, lim_agua))
        cap_g = max(0.0, min(pt, (va - va_res) * k_t / dt))
        o["cap_bombeamento"][t], o["cap_geracao"][t] = cap_b, cap_g
        o["lim_armaz_cheio"][t] = lim_espaco < pb - TOL and lim_espaco <= lim_agua
        o["lim_montante_vazio"][t] = lim_agua < pb - TOL and lim_agua < lim_espaco
        o["lim_armaz_vazio"][t] = cap_g < pt - TOL

        # 3) decisão: com excesso na rede, bombeia; senão, gera se houver demanda
        if excesso[t] > TOL:
            pbomb = min(excesso[t], cap_b)
            vol = pbomb * dt * k_b
            va += vol
            vm -= vol
            o["bombeamento"][t], o["vol_bombeado"][t] = pbomb, vol
        else:
            alvo_term = demanda_term[t] if usar_term else 0.0
            alvo = max(alvo_term, pt if ponta[t] else 0.0)
            pger = min(alvo, cap_g)
            if pger > TOL:
                vol = pger * dt / k_t
                va -= vol
                vm += vol
                if vm > vm_max:
                    o["vertido_montante"][t] += vm - vm_max
                    vm = vm_max
                o["geracao"][t], o["vol_turbinado"][t] = pger, vol
                o["termica_deslocada"][t] = min(pger, alvo_term)
                o["ger_ponta"][t] = pger - o["termica_deslocada"][t]
        va = min(max(va, 0.0), va_max)
        o["vol_armazenamento"][t], o["vol_montante"][t] = va, vm

    s = pd.DataFrame(index=e.index)
    for c in ("vertimento", "corte_fv", "corte_eolica", "excedente", "termica_flex",
              "deficit", "carga_residual"):
        s[c] = e[c]
    s["excesso_disponivel"] = excesso
    s["bombeamento"] = o["bombeamento"]
    s["geracao"] = o["geracao"]
    s["curtailment_absorvido"] = o["bombeamento"]
    s["curtailment_remanescente"] = excesso - o["bombeamento"]
    s["termica_deslocada"] = o["termica_deslocada"]
    s["geracao_ponta"] = o["ger_ponta"]
    s["cap_bombeamento"] = o["cap_bombeamento"]
    s["cap_geracao"] = o["cap_geracao"]
    s["vol_armazenamento_hm3"] = o["vol_armazenamento"] / HM3
    s["vol_montante_hm3"] = o["vol_montante"] / HM3
    s["nivel_armazenamento"] = o["vol_armazenamento"] / va_max
    s["nivel_montante"] = o["vol_montante"] / vm_max
    s["energia_armazenada_MWh"] = o["vol_armazenamento"] * k_t
    s["autonomia_h"] = np.maximum(o["vol_armazenamento"] - va_res, 0) * k_t / pt
    s["vertido_montante_hm3"] = o["vertido_montante"] / HM3
    s["estado"] = np.where(o["bombeamento"] > TOL, "bombeando",
                           np.where(o["geracao"] > TOL, "gerando", "parada"))
    s["cenario"] = classificar_cenarios(e)
    s["lim_armazenamento_cheio"] = o["lim_armaz_cheio"].astype(bool)
    s["lim_montante_vazio"] = o["lim_montante_vazio"].astype(bool)
    s["lim_armazenamento_vazio"] = o["lim_armaz_vazio"].astype(bool)

    balanco = _balanco(s, o, p, dt, va0, vm0, va, vm, fontes, limiar_ponta)
    disp = _disponibilidade(s, p, dt)
    return ResultadoUsina(s, balanco, disp, p, dt)


def _balanco(s, o, p, dt, va0, vm0, va, vm, fontes, limiar_ponta) -> dict:
    k_t = p.mwh_por_m3_turbinado
    e_bomb = float(s["bombeamento"].sum() * dt)
    e_ger = float(s["geracao"].sum() * dt)
    e_ini, e_fim = va0 * k_t, va * k_t
    d_est = e_fim - e_ini
    perdas = e_bomb - e_ger - d_est
    exc = {c: float(s[c].sum() * dt) for c in ("vertimento", "corte_fv", "corte_eolica",
                                                "excedente")}
    exc_total = float(s["excesso_disponivel"].sum() * dt)
    # rateio do absorvido entre componentes, proporcional à participação em cada passo
    absorvido = {}
    tot_passo = s["excesso_disponivel"].replace(0, np.nan)
    for c in fontes:
        absorvido[c] = float((s["bombeamento"] * s[c] / tot_passo).fillna(0).sum() * dt)
    fech = e_bomb * p.rend_ciclo - (e_ger + d_est)
    return {
        "periodo_inicio": str(s.index[0]), "periodo_fim": str(s.index[-1]),
        "passo_h": dt, "duracao_h": dt * len(s),
        "potencia_turbina_efetiva_MW": p.pot_turbina_ef_mw,
        "potencia_bomba_efetiva_MW": p.pot_bomba_ef_mw,
        "rendimento_ciclo": p.rend_ciclo,
        "energia_armazenavel_max_MWh": p.energia_armazenavel_mwh,
        "energia": {
            "bombeada_consumida_MWh": e_bomb,
            "gerada_MWh": e_ger,
            "estoque_inicial_MWh": e_ini,
            "estoque_final_MWh": e_fim,
            "variacao_estoque_MWh": d_est,
            "perdas_MWh": perdas,
            "rendimento_ciclo_realizado": ((e_ger + d_est) / e_bomb) if e_bomb > 0 else None,
            "fechamento_erro_MWh": fech,
        },
        "curtailment": {
            "fontes_consideradas": list(fontes),
            "excesso_por_componente_MWh": exc,
            "excesso_considerado_MWh": exc_total,
            "absorvido_MWh": e_bomb,
            "absorvido_pct": 100 * e_bomb / exc_total if exc_total > 0 else None,
            "absorvido_por_componente_MWh": absorvido,
            "remanescente_MWh": exc_total - e_bomb,
        },
        "geracao": {
            "termica_flex_rede_MWh": float(s["termica_flex"].sum() * dt),
            "deficit_rede_MWh": float(s["deficit"].sum() * dt),
            "termica_ou_deficit_deslocado_MWh": float(s["termica_deslocada"].sum() * dt),
            "geracao_ponta_MWh": float(s["geracao_ponta"].sum() * dt),
            "limiar_ponta_carga_residual_MW": limiar_ponta,
            "politica_geracao": p.politica_geracao,
        },
        "agua_hm3": {
            "bombeado": float(o["vol_bombeado"].sum() / HM3),
            "turbinado": float(o["vol_turbinado"].sum() / HM3),
            "vertido_montante": float(o["vertido_montante"].sum() / HM3),
            "defluencia_nao_atendida": float(o["defluencia_nao_atendida"].sum() / HM3),
            "armazenamento_inicial": va0 / HM3, "armazenamento_final": va / HM3,
            "montante_inicial": vm0 / HM3, "montante_final": vm / HM3,
        },
        "ciclos_equivalentes": e_ger / p.energia_armazenavel_mwh,
    }


def _indicadores(g: pd.DataFrame, p: ParametrosUsina, dt: float) -> dict:
    n = len(g)
    if n == 0:
        return {"passos": 0, "horas": 0.0}
    pb, pt = p.pot_bomba_ef_mw, p.pot_turbina_ef_mw
    cb, cg = g["cap_bombeamento"], g["cap_geracao"]
    absorvivel = np.minimum(g["excesso_disponivel"], pb).sum()
    pct = lambda m: float(100 * m.mean())  # noqa: E731
    return {
        "passos": int(n), "horas": float(n * dt),
        "excesso_MWh": float(g["excesso_disponivel"].sum() * dt),
        "absorvido_MWh": float(g["bombeamento"].sum() * dt),
        "absorvido_pct_do_excesso": (float(100 * g["bombeamento"].sum()
                                           / g["excesso_disponivel"].sum())
                                     if g["excesso_disponivel"].sum() > 0 else None),
        "disponibilidade_absorcao_pct": (float(100 * g["bombeamento"].sum() / absorvivel)
                                         if absorvivel > 0 else None),
        "bombeamento_disp_plena_pct": pct(cb >= pb - TOL),
        "bombeamento_disp_parcial_pct": pct((cb > TOL) & (cb < pb - TOL)),
        "bombeamento_indisponivel_pct": pct(cb <= TOL),
        "geracao_disp_plena_pct": pct(cg >= pt - TOL),
        "geracao_indisponivel_pct": pct(cg <= TOL),
        "autonomia_media_h": float(g["autonomia_h"].mean()),
        "autonomia_min_h": float(g["autonomia_h"].min()),
        "fator_capacidade_bombeamento_pct": float(100 * g["bombeamento"].mean() / pb),
        "fator_capacidade_geracao_pct": float(100 * g["geracao"].mean() / pt),
        "limitado_armazenamento_cheio_pct": pct(g["lim_armazenamento_cheio"]),
        "limitado_montante_vazio_pct": pct(g["lim_montante_vazio"]),
    }


def _disponibilidade(s: pd.DataFrame, p: ParametrosUsina, dt: float) -> dict:
    out = {"periodo_total": _indicadores(s, p, dt)}
    for cen, m in mascaras_cenarios(s).items():
        out[cen] = _indicadores(s[m], p, dt)
    return out

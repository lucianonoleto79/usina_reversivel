"""Gravação dos resultados: uma pasta por simulação."""
from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime
from pathlib import Path

from .graficos import grafico_operacao


def nome_pasta(*partes: str, carimbo: bool = True) -> str:
    """Nome de pasta legível e seguro: partes + data/hora da execução."""
    txt = "_".join(str(p) for p in partes if p not in (None, ""))
    txt = re.sub(r"[^0-9A-Za-z._-]+", "-", txt).strip("-_")
    if carimbo:
        txt += "_" + datetime.now().strftime("%Y%m%d-%H%M%S")
    return txt


def _json(obj, arquivo: Path):
    with open(arquivo, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, default=str)


def salvar_resultado(res, pasta: Path, titulo: str = "", origem: str = "") -> Path:
    """Grava ``serie_usina.csv``, ``balanco.json``, ``disponibilidade.json``,
    ``parametros.json`` e ``operacao.png`` em ``pasta``."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    res.serie.to_csv(pasta / "serie_usina.csv")
    _json(res.balanco, pasta / "balanco.json")
    _json(res.disponibilidade, pasta / "disponibilidade.json")
    par = dataclasses.asdict(res.params)
    par.update(origem_serie=origem,
               potencia_turbina_efetiva_MW=res.params.pot_turbina_ef_mw,
               potencia_bomba_efetiva_MW=res.params.pot_bomba_ef_mw,
               avisos=res.params.avisos())
    _json(par, pasta / "parametros.json")
    grafico_operacao(res, pasta / "operacao.png", titulo)
    return pasta


def linha_comparacao(nome: str, res) -> dict:
    b, d = res.balanco, res.disponibilidade
    return {
        "serie": nome,
        "excesso_MWh": b["curtailment"]["excesso_considerado_MWh"],
        "bombeada_MWh": b["energia"]["bombeada_consumida_MWh"],
        "gerada_MWh": b["energia"]["gerada_MWh"],
        "perdas_MWh": b["energia"]["perdas_MWh"],
        "variacao_estoque_MWh": b["energia"]["variacao_estoque_MWh"],
        "absorvido_pct": b["curtailment"]["absorvido_pct"],
        "termica_deslocada_MWh": b["geracao"]["termica_ou_deficit_deslocado_MWh"],
        "disp_absorcao_curtailment_pct":
            d["curtailment_hidraulico"].get("disponibilidade_absorcao_pct"),
        "disp_absorcao_intermitente_pct":
            d["excesso_intermitente"].get("disponibilidade_absorcao_pct"),
        "geracao_disp_plena_pct": d["periodo_total"]["geracao_disp_plena_pct"],
        "autonomia_media_h": d["periodo_total"]["autonomia_media_h"],
    }

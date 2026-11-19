"""Leitura da série do curtailment_simulator (``serie.csv``)."""
from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd

OBRIGATORIAS = ("vertimento", "corte_fv", "corte_eolica")
OPCIONAIS = ("excedente", "termica_flex", "deficit", "carga_residual")


def ler_serie_curtailment(caminho: str | Path) -> pd.DataFrame:
    """Lê o ``serie.csv`` gerado pelo curtailment_simulator.

    ``caminho`` pode ser o arquivo ou a pasta da rodada que o contém. Devolve um
    DataFrame indexado por instante com as colunas usadas pela usina (MW médios).
    """
    caminho = Path(caminho)
    if caminho.is_dir():
        caminho = caminho / "serie.csv"
    if not caminho.exists():
        raise FileNotFoundError(f"serie.csv não encontrado: {caminho}")
    df = pd.read_csv(caminho, index_col=0, parse_dates=True)
    return preparar_serie(df, origem=str(caminho))


def preparar_serie(df: pd.DataFrame, origem: str = "DataFrame") -> pd.DataFrame:
    """Valida e normaliza uma série no formato do ``serie.csv``."""
    faltam = [c for c in OBRIGATORIAS if c not in df]
    if faltam:
        raise ValueError(f"{origem}: colunas obrigatórias ausentes {faltam} "
                         "(esperado o serie.csv do curtailment_simulator)")
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df.index = pd.to_datetime(df.index)
    if len(df) < 2:
        raise ValueError(f"{origem}: a série precisa de pelo menos 2 instantes")
    out = pd.DataFrame(index=df.index)
    for c in OBRIGATORIAS + OPCIONAIS:
        if c in df:
            out[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0).clip(lower=0.0) \
                if c != "carga_residual" else pd.to_numeric(df[c], errors="coerce")
        else:
            if c != "excedente":
                warnings.warn(f"{origem}: coluna '{c}' ausente; considerada nula.")
            out[c] = 0.0
    out["carga_residual"] = out["carga_residual"].ffill().bfill().fillna(0.0)
    out.index.name = "instante"
    return out.sort_index()


def passo_horas(idx: pd.DatetimeIndex) -> float:
    """Duração do passo (h), pela mediana dos intervalos."""
    return float(pd.Series(idx).diff().dropna().median() / pd.Timedelta(hours=1))

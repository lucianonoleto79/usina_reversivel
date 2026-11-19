"""Gráfico da operação da usina reversível."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def grafico_operacao(res, arquivo: Path, titulo: str = "") -> Path:
    s, p = res.serie, res.params
    fig, ax = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    a = ax[0]
    a.fill_between(s.index, s["excesso_disponivel"].clip(upper=p.pot_bomba_ef_mw * 3),
                   step="post", alpha=0.25, color="tab:orange",
                   label="Excesso na rede (cortado em 3×P_bomba)")
    a.step(s.index, s["bombeamento"], where="post", color="tab:blue", label="Bombeamento")
    a.step(s.index, -s["geracao"], where="post", color="tab:green",
           label="Geração (negativa)")
    a.axhline(0, color="k", lw=0.5)
    a.set_ylabel("MW")
    a.set_title("Operação: bombeamento (+) e geração (−)")
    a.legend(loc="upper left", fontsize=8)

    a = ax[1]
    a.plot(s.index, 100 * s["nivel_armazenamento"], color="tab:blue",
           label="Reservatório de armazenamento")
    a.plot(s.index, 100 * s["nivel_montante"], color="tab:brown",
           label="Reservatório de montante")
    if p.reserva_min > 0:
        a.axhline(100 * p.reserva_min, color="tab:blue", ls=":", lw=1, label="Reserva mínima")
    a.set_ylabel("% do volume útil")
    a.set_ylim(-2, 102)
    a.set_title("Volumes dos reservatórios")
    a.legend(loc="upper left", fontsize=8)

    a = ax[2]
    a.step(s.index, s["cap_bombeamento"], where="post", color="tab:blue",
           label="Capacidade disponível p/ bombear")
    a.step(s.index, s["cap_geracao"], where="post", color="tab:green",
           label="Capacidade disponível p/ gerar")
    a.set_ylabel("MW")
    a.set_title("Disponibilidade da usina")
    a.legend(loc="upper left", fontsize=8)

    if titulo:
        fig.suptitle(titulo, fontsize=11)
    fig.tight_layout()
    fig.savefig(arquivo, dpi=120)
    plt.close(fig)
    return Path(arquivo)

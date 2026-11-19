#!/usr/bin/env python3
"""Simulação da operação de uma usina reversível em cascata com UHE.

A usina bombeia água do reservatório de montante (UHE existente) para o
reservatório de armazenamento quando há excesso de energia na rede
(curtailment hidráulico ou excesso de eólica/FV) e turbina de volta quando há
térmica de rampa/ponta a deslocar, déficit ou carga residual de ponta.

A série de excesso vem do ``serie.csv`` do curtailment_simulator.

Exemplo:
    python main.py --serie ../curtailment_simulator/outputs/<rodada> \\
        --vazao 200 --queda 300 --pot-turbina 530 --pot-bomba 670 \\
        --vol-montante 50 --vol-armazenamento 10
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

from usina_reversivel import ParametrosUsina, ler_serie_curtailment, simular
from usina_reversivel.config import FONTES_EXCESSO, POLITICAS_GERACAO
from usina_reversivel.saidas import linha_comparacao, nome_pasta, salvar_resultado


def argumentos(argv=None):
    a = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--serie", nargs="+", required=True,
                   help="serie.csv do curtailment_simulator, ou a pasta da rodada "
                        "(várias = comparação de cenários)")
    g = a.add_argument_group("usina (obrigatórios)")
    g.add_argument("--vazao", type=float, required=True, help="vazão nominal (m³/s)")
    g.add_argument("--queda", type=float, required=True, help="queda líquida (m)")
    g.add_argument("--pot-turbina", type=float, required=True,
                   help="potência de turbinamento (MW)")
    g.add_argument("--pot-bomba", type=float, required=True,
                   help="potência de bombeamento (MW)")
    g.add_argument("--vol-montante", type=float, required=True,
                   help="volume útil do reservatório de montante (hm³)")
    g.add_argument("--vol-armazenamento", type=float, required=True,
                   help="volume útil do reservatório de armazenamento (hm³)")
    o = a.add_argument_group("opcionais")
    o.add_argument("--rend-turbina", type=float, default=0.90)
    o.add_argument("--rend-bomba", type=float, default=0.88)
    o.add_argument("--nivel-ini-montante", type=float, default=0.5,
                   help="fração inicial do volume útil do montante (0-1)")
    o.add_argument("--nivel-ini-armazenamento", type=float, default=0.0,
                   help="fração inicial do volume útil do armazenamento (0-1)")
    o.add_argument("--afluencia", type=float, default=0.0,
                   help="afluência natural ao montante (m³/s)")
    o.add_argument("--defluencia-uhe", type=float, default=0.0,
                   help="defluência da UHE a partir do montante (m³/s)")
    o.add_argument("--reserva-min", type=float, default=0.0,
                   help="fração do armazenamento mantida como reserva (0-1)")
    o.add_argument("--fontes-bombeamento", nargs="+", choices=FONTES_EXCESSO,
                   default=list(FONTES_EXCESSO),
                   help="componentes do serie.csv que a usina pode absorver")
    o.add_argument("--politica-geracao", choices=POLITICAS_GERACAO, default="termica_ponta",
                   help="termica: desloca térmica flex/déficit; ponta: gera na ponta da "
                        "carga residual; termica_ponta: ambos")
    o.add_argument("--percentil-ponta", type=float, default=0.90,
                   help="percentil da carga residual que define a ponta (0-1)")
    o.add_argument("--saida", default="outputs", help="pasta de saída")
    return a.parse_args(argv)


def fmt(v):
    if v is None or (isinstance(v, float) and v != v):
        return "-"
    if isinstance(v, float):
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(v)


def imprimir(rotulo, res):
    b, d = res.balanco, res.disponibilidade
    en, cu, ge = b["energia"], b["curtailment"], b["geracao"]
    print(f"\n=== {rotulo} ===")
    print(f"  Período: {b['periodo_inicio']} a {b['periodo_fim']} (passo {fmt(b['passo_h'])} h)")
    print("  -- Balanço de energia --")
    for k, v in [("Energia bombeada (consumida) MWh", en["bombeada_consumida_MWh"]),
                 ("Energia gerada MWh", en["gerada_MWh"]),
                 ("Variação do estoque MWh", en["variacao_estoque_MWh"]),
                 ("Perdas MWh", en["perdas_MWh"]),
                 ("Rendimento de ciclo realizado", en["rendimento_ciclo_realizado"]),
                 ("Excesso considerado na rede MWh", cu["excesso_considerado_MWh"]),
                 ("Curtailment absorvido %", cu["absorvido_pct"]),
                 ("Térmica/déficit deslocado MWh", ge["termica_ou_deficit_deslocado_MWh"]),
                 ("Geração na ponta MWh", ge["geracao_ponta_MWh"]),
                 ("Ciclos equivalentes", b["ciclos_equivalentes"])]:
        print(f"  {k:36s} {fmt(v)}")
    print("  -- Disponibilidade --")
    cab = ["horas", "absorvido_pct_do_excesso", "disponibilidade_absorcao_pct",
           "bombeamento_disp_plena_pct", "geracao_disp_plena_pct", "autonomia_media_h",
           "limitado_armazenamento_cheio_pct", "limitado_montante_vazio_pct"]
    tab = pd.DataFrame({c: {k: d[c].get(k) for k in cab} for c in d})
    print(tab.map(fmt).to_string())


def main(argv=None):
    args = argumentos(argv)
    try:
        p = ParametrosUsina(
            vazao_m3s=args.vazao, queda_m=args.queda,
            pot_turbina_mw=args.pot_turbina, pot_bomba_mw=args.pot_bomba,
            vol_montante_hm3=args.vol_montante, vol_armazenamento_hm3=args.vol_armazenamento,
            rend_turbina=args.rend_turbina, rend_bomba=args.rend_bomba,
            nivel_ini_montante=args.nivel_ini_montante,
            nivel_ini_armazenamento=args.nivel_ini_armazenamento,
            afluencia_m3s=args.afluencia, defluencia_uhe_m3s=args.defluencia_uhe,
            reserva_min=args.reserva_min, fontes_bombeamento=args.fontes_bombeamento,
            politica_geracao=args.politica_geracao, percentil_ponta=args.percentil_ponta)
    except ValueError as e:
        sys.exit(f"ERRO: {e}")
    for m in p.avisos():
        warnings.warn(m)

    raiz = Path(args.saida)
    multiplas = len(args.serie) > 1
    if multiplas:
        raiz = raiz / nome_pasta("comparacao", f"{len(args.serie)}series")
    linhas = []
    for caminho in args.serie:
        try:
            entrada = ler_serie_curtailment(caminho)
        except (FileNotFoundError, ValueError) as e:
            sys.exit(f"ERRO: {e}")
        nome = Path(caminho).name if Path(caminho).is_dir() else Path(caminho).parent.name
        nome = nome or Path(caminho).stem
        res = simular(entrada, p)
        pasta = raiz / nome_pasta("usina", nome, carimbo=not multiplas)
        salvar_resultado(res, pasta, f"Usina reversível — {nome}", origem=str(caminho))
        imprimir(nome, res)
        print(f"  Resultados em {pasta}/")
        linhas.append(linha_comparacao(nome, res))
    if multiplas:
        comp = pd.DataFrame(linhas)
        comp.to_csv(raiz / "comparacao.csv", index=False)
        print("\n=== Comparação de cenários ===")
        print(comp.set_index("serie").T.map(fmt).to_string())
        print(f"\nComparação em {raiz}/comparacao.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

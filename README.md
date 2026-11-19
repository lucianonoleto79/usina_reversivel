# usina_reversivel

Simulador em Python da operação de uma **usina reversível (hidrelétrica de
bombeamento)** que armazena o excesso de energia da rede, em cenários de
**curtailment hidráulico** (vertimento turbinável) e de **excesso de energia
intermitente** (corte de eólica/FV). A série de excesso vem do
[`curtailment_simulator`](../curtailment_simulator) (arquivo `serie.csv`).

## 1. Arranjo modelado: cascata com UHE

```
             bombeamento (excesso da rede)
   ┌───────────────────────────────────────────┐
   │                                           ▼
 [Reservatório de montante]            [Reservatório de armazenamento]
  (UHE existente, afluência natural)     (superior, H metros acima)
   ▲                                           │
   └───────────────────────────────────────────┘
             turbinamento (desloca térmica / ponta)
```

- **Reservatório de montante**: reservatório de uma UHE existente. Recebe a
  afluência natural e perde a defluência da UHE (ambas opcionais, constantes).
- **Reservatório de armazenamento**: reservatório superior. A usina bombeia água
  do montante para ele quando sobra energia na rede, e turbina de volta para o
  montante quando a rede precisa.
- A **queda** é o desnível líquido entre os dois reservatórios.

## 2. Instalação e execução

Requer Python ≥ 3.10.

```bash
pip install -r requirements.txt
```

Gere uma série no simulador de curtailment (exemplo offline, com o dobro de MMGD):

```bash
cd ../curtailment_simulator && python main.py --fonte sintetico --frota ilustrativa --escala-mmgd 2
```

Simule a usina reversível sobre a pasta gerada (valores **ilustrativos**, não de uma usina real):

```bash
python main.py --serie ../curtailment_simulator/outputs/<pasta_da_rodada> --vazao 200 --queda 300 --pot-turbina 530 --pot-bomba 670 --vol-montante 50 --vol-armazenamento 5
```

Passando várias séries em `--serie` (ex.: MMGD x1 e x2), cada uma é simulada e é
gerado um `comparacao.csv`.

Testes:

```bash
python -m pytest -q tests
```

## 3. Entradas

| Opção | Unidade | Descrição |
|---|---|---|
| `--serie` | – | `serie.csv` do curtailment_simulator ou a pasta da rodada (um ou mais) |
| `--vazao` | m³/s | Vazão nominal da máquina |
| `--queda` | m | Queda líquida (constante) |
| `--pot-turbina` | MW | Potência de turbinamento |
| `--pot-bomba` | MW | Potência de bombeamento |
| `--vol-montante` | hm³ | Volume útil do reservatório de montante |
| `--vol-armazenamento` | hm³ | Volume útil do reservatório de armazenamento |
| `--rend-turbina` / `--rend-bomba` | – | Rendimentos (padrão 0,90 / 0,88; valores típicos, ajuste ao seu projeto) |
| `--nivel-ini-montante` / `--nivel-ini-armazenamento` | fração | Níveis iniciais (padrão 0,5 / 0) |
| `--afluencia` / `--defluencia-uhe` | m³/s | Balanço natural do montante (padrão 0) |
| `--reserva-min` | fração | Parte do armazenamento que não é gerada (reserva de disponibilidade) |
| `--fontes-bombeamento` | – | Quais colunas a usina absorve: `vertimento corte_fv corte_eolica excedente` (padrão: todas) |
| `--politica-geracao` | – | `termica` (desloca `termica_flex` + `deficit`), `ponta` (gera a plena potência quando a `carga_residual` passa do percentil), `termica_ponta` (padrão) |
| `--percentil-ponta` | 0–1 | Percentil da carga residual que define a ponta (padrão 0,90) |
| `--saida` | – | Pasta de resultados (padrão `outputs`) |

Colunas do `serie.csv` usadas: `vertimento`, `corte_fv`, `corte_eolica`
(obrigatórias), `excedente`, `termica_flex`, `deficit`, `carga_residual`
(opcionais; ausentes = 0). O passo de tempo é lido do índice (15 ou 30 min).

## 4. Modelo

- Potência hidráulica: `P = ρ·g·Q·H`. Geração máxima `min(P_turbina, ρgQH·η_t)`;
  bombeamento máximo `min(P_bomba, ρgQH/η_b)`. Se a potência informada diferir
  mais de 10% do valor físico, o programa avisa e usa o menor.
- Energia armazenada (equivalente gerável): `E = V·ρ·g·H·η_t / 3,6·10⁹` MWh.
- A cada passo:
  1. aplica afluência − defluência ao montante (o que exceder o volume útil é vertido);
  2. calcula a **capacidade disponível** para bombear (limitada pela potência, pelo
     espaço livre no armazenamento e pela água no montante) e para gerar (limitada
     pela potência e pelo estoque acima da reserva);
  3. se há excesso na rede, **bombeia** `min(excesso, capacidade)`; senão,
     **gera** conforme a política. Não bombeia e gera no mesmo passo.
- Fechamento verificado: `E_bombeada·η_t·η_b = E_gerada + ΔE_estoque`.

## 5. Saídas (uma pasta por rodada)

- **`balanco.json`** — balanço de energia: energia bombeada (consumida), gerada,
  variação do estoque, perdas, rendimento de ciclo realizado, erro de fechamento;
  curtailment total por componente, absorvido (MWh e %) e remanescente; térmica
  ou déficit deslocado e geração na ponta; balanço hídrico (hm³); ciclos equivalentes.
- **`disponibilidade.json`** — para o período todo e para os passos de cada cenário
  (`curtailment_hidraulico`: vertimento > 0; `excesso_intermitente`: corte eólica/FV
  ou excedente > 0; `sem_excesso`). Os dois primeiros podem se sobrepor (na política
  `combinada` do curtailment_simulator, vertimento e corte acontecem juntos):
  - `absorvido_pct_do_excesso`: energia absorvida / excesso na rede;
  - `disponibilidade_absorcao_pct`: energia absorvida / energia que a usina absorveria
    se estivesse sempre disponível (`Σ min(excesso, P_bomba)`);
  - `% do tempo` com bombeamento disponível pleno/parcial/nulo e geração plena;
  - autonomia média/mínima a plena potência (h); fatores de capacidade;
  - `% do tempo` limitado por armazenamento cheio ou montante vazio.
- **`serie_usina.csv`** — série por passo: excesso, bombeamento, geração,
  absorvido/remanescente, volumes e níveis, energia armazenada, autonomia,
  capacidades disponíveis, estado (bombeando/gerando/parada) e cenário.
- **`operacao.png`** — operação, volumes e disponibilidade.
- **`parametros.json`** — parâmetros usados e avisos.
- **`comparacao.csv`** — só com várias séries.

## 6. Limitações

- Queda constante (não varia com o nível dos reservatórios) e rendimentos constantes.
- Afluência e defluência da UHE constantes; a geração da própria UHE não é modelada.
- Despacho guloso, sem otimização nem previsão: bombeia sempre que há excesso e
  gera sempre que há sinal de demanda.
- O excesso do `serie.csv` é o do SIN inteiro; a usina absorve apenas o que cabe
  na sua potência, sem restrições de transmissão.
- Os valores de exemplo são ilustrativos; confira os dados do seu projeto.

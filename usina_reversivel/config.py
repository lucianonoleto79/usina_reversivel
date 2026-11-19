"""Parâmetros da usina reversível e constantes físicas."""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

RHO = 1000.0   # massa específica da água (kg/m³)
G = 9.81       # aceleração da gravidade (m/s²)
HM3 = 1e6      # m³ por hm³

FONTES_EXCESSO = ("vertimento", "corte_fv", "corte_eolica", "excedente")
POLITICAS_GERACAO = ("termica", "ponta", "termica_ponta")


@dataclass
class ParametrosUsina:
    """Arranjo em cascata com uma UHE existente.

    * reservatório de **montante**: reservatório da UHE (recebe afluência natural);
      a usina reversível bombeia dele e turbina de volta para ele;
    * reservatório de **armazenamento**: reservatório superior, enchido por bombeamento
      com o excesso de energia da rede. A ``queda`` é o desnível entre os dois.
    """
    vazao_m3s: float               # vazão nominal da máquina (m³/s)
    queda_m: float                 # queda líquida (m)
    pot_turbina_mw: float          # potência nominal em turbinamento (MW)
    pot_bomba_mw: float            # potência nominal em bombeamento (MW)
    vol_montante_hm3: float        # volume útil do reservatório de montante (hm³)
    vol_armazenamento_hm3: float   # volume útil do reservatório de armazenamento (hm³)

    rend_turbina: float = 0.90
    rend_bomba: float = 0.88
    nivel_ini_montante: float = 0.5        # fração do volume útil
    nivel_ini_armazenamento: float = 0.0   # fração do volume útil
    afluencia_m3s: float = 0.0             # afluência natural ao montante
    defluencia_uhe_m3s: float = 0.0        # defluência da UHE a partir do montante
    reserva_min: float = 0.0               # fração do armazenamento reservada (não gerada)
    fontes_bombeamento: tuple = field(default_factory=lambda: FONTES_EXCESSO)
    politica_geracao: str = "termica_ponta"
    percentil_ponta: float = 0.90

    def __post_init__(self):
        for nome in ("vazao_m3s", "queda_m", "pot_turbina_mw", "pot_bomba_mw",
                     "vol_montante_hm3", "vol_armazenamento_hm3"):
            if getattr(self, nome) <= 0:
                raise ValueError(f"{nome} deve ser > 0")
        for nome in ("rend_turbina", "rend_bomba"):
            if not 0 < getattr(self, nome) <= 1:
                raise ValueError(f"{nome} deve estar em (0, 1]")
        for nome in ("nivel_ini_montante", "nivel_ini_armazenamento", "reserva_min",
                     "percentil_ponta"):
            if not 0 <= getattr(self, nome) <= 1:
                raise ValueError(f"{nome} deve estar em [0, 1]")
        if self.afluencia_m3s < 0 or self.defluencia_uhe_m3s < 0:
            raise ValueError("afluência e defluência devem ser >= 0")
        self.fontes_bombeamento = tuple(self.fontes_bombeamento)
        invalidas = set(self.fontes_bombeamento) - set(FONTES_EXCESSO)
        if invalidas or not self.fontes_bombeamento:
            raise ValueError(f"fontes_bombeamento deve ser subconjunto de {FONTES_EXCESSO}")
        if self.politica_geracao not in POLITICAS_GERACAO:
            raise ValueError(f"politica_geracao deve ser uma de {POLITICAS_GERACAO}")

    # ---- grandezas derivadas -------------------------------------------------
    @property
    def pot_hidraulica_mw(self) -> float:
        """ρ·g·Q·H (MW): potência hidráulica com a vazão nominal."""
        return RHO * G * self.vazao_m3s * self.queda_m / 1e6

    @property
    def pot_turbina_ef_mw(self) -> float:
        """Potência máxima de geração: limitada pela máquina e por ρgQHη_t."""
        return min(self.pot_turbina_mw, self.pot_hidraulica_mw * self.rend_turbina)

    @property
    def pot_bomba_ef_mw(self) -> float:
        """Potência máxima de bombeamento: limitada pela máquina e por ρgQH/η_b."""
        return min(self.pot_bomba_mw, self.pot_hidraulica_mw / self.rend_bomba)

    @property
    def mwh_por_m3_turbinado(self) -> float:
        """Energia elétrica gerada por m³ turbinado (MWh/m³)."""
        return RHO * G * self.queda_m * self.rend_turbina / 3.6e9

    @property
    def m3_por_mwh_bombeado(self) -> float:
        """Volume elevado por MWh consumido no bombeamento (m³/MWh)."""
        return 3.6e9 * self.rend_bomba / (RHO * G * self.queda_m)

    @property
    def rend_ciclo(self) -> float:
        return self.rend_turbina * self.rend_bomba

    @property
    def energia_armazenavel_mwh(self) -> float:
        """Energia gerável com o reservatório de armazenamento cheio (MWh)."""
        return self.vol_armazenamento_hm3 * HM3 * self.mwh_por_m3_turbinado

    def avisos(self) -> list[str]:
        """Inconsistências entre potência informada e ρgQHη (tolerância de 10%)."""
        msgs = []
        pt = self.pot_hidraulica_mw * self.rend_turbina
        pb = self.pot_hidraulica_mw / self.rend_bomba
        if abs(pt - self.pot_turbina_mw) > 0.10 * self.pot_turbina_mw:
            msgs.append(f"Potência de turbinamento informada ({self.pot_turbina_mw:.1f} MW) difere "
                        f"de ρgQHη_t = {pt:.1f} MW; usando o menor valor ({self.pot_turbina_ef_mw:.1f} MW).")
        if abs(pb - self.pot_bomba_mw) > 0.10 * self.pot_bomba_mw:
            msgs.append(f"Potência de bombeamento informada ({self.pot_bomba_mw:.1f} MW) difere "
                        f"de ρgQH/η_b = {pb:.1f} MW; usando o menor valor ({self.pot_bomba_ef_mw:.1f} MW).")
        return msgs

    def emitir_avisos(self):
        for m in self.avisos():
            warnings.warn(m, stacklevel=3)

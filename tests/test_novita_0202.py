"""Novita' della 0.20.2: cartella dei dati su Linux ~/.local/share/CashFlowAnalyzer (spostata da sola da
spese-app) e chiave di Claude nel portachiavi con il nome CashFlowAnalyzer (spostata da spese-app)."""
import os
import sys
from pathlib import Path

import pytest

from spese import llm, percorsi


@pytest.fixture()
def linux(monkeypatch, tmp_path):
    """Linux senza SPESE_DATI, con XDG_DATA_HOME in una cartella di prova."""
    monkeypatch.delenv("SPESE_DATI", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    return tmp_path / "share"


def _vecchia(base, db=True, **file):
    v = base / "spese-app"
    v.mkdir(parents=True)
    if db:
        (v / "spese.db").write_bytes(b"cifrato")
    (v / "backup_20261101_120000.db").write_bytes(b"backup")
    for nome, testo in file.items():
        (v / nome).write_text(testo)
    return v


def test_cartella_vecchia_spostata_al_primo_avvio(linux):
    v = _vecchia(linux)
    p = percorsi.cartella_dati()
    assert p == linux / "CashFlowAnalyzer" and not v.exists()
    assert sorted(x.name for x in p.iterdir()) == ["backup_20261101_120000.db", "spese.db"]
    assert percorsi.file_database().read_bytes() == b"cifrato"
    assert percorsi.cartella_dati() == p                                # dal secondo avvio: niente da spostare


@pytest.mark.skipif(not Path("/proc/self").exists(),
                    reason="processi vivi letti da /proc: solo Linux, dove vale lo spostamento")
def test_non_si_sposta_con_server_o_icona_accesi(linux):
    v = _vecchia(linux, **{"server.pid": "%d\n" % os.getpid()})       # processo vivo: la cartella e' in uso
    assert percorsi.cartella_dati() == v and v.exists() and not (linux / "CashFlowAnalyzer").exists()
    (v / "server.pid").write_text("999999999\n")                       # pid rimasto da un arresto brusco
    assert percorsi.cartella_dati() == linux / "CashFlowAnalyzer" and not v.exists()


def test_due_cartelle_vince_quella_con_il_database(linux):
    v = _vecchia(linux)
    nuova = linux / "CashFlowAnalyzer"
    nuova.mkdir()                                                       # vuota (es. creata da un avvio a meta')
    assert percorsi.cartella_dati() == v
    (nuova / "spese.db").write_bytes(b"nuovo")
    assert percorsi.cartella_dati() == nuova
    assert (v / "spese.db").exists()                                    # non si cancella nulla


def test_con_spese_dati_non_si_sposta_nulla(linux, monkeypatch, tmp_path):
    v = _vecchia(linux)
    monkeypatch.setenv("SPESE_DATI", str(tmp_path / "altrove"))
    assert percorsi.cartella_dati() == tmp_path / "altrove" and v.exists()


def test_windows_nessuna_cartella_vecchia(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert percorsi.cartella_vecchia() is None
    assert percorsi.scegli_cartella("C:/nuova", None) == Path("C:/nuova")


def test_icona_usa_la_cartella_dell_app():
    """tray.py (Python di sistema) non ha piu' un percorso suo: usa spese.percorsi, come app e avvio."""
    testo = (Path(percorsi.RADICE) / "tray.py").read_text(encoding="utf-8")
    assert "from spese.percorsi import cartella_dati" in testo and "spese-app" not in testo


# --- chiave di Claude nel portachiavi ---------------------------------------------------------------------------
@pytest.fixture()
def portachiavi(monkeypatch):
    """Portachiavi finto: {(servizio, utente): chiave}. guasto = {"set"} fa fallire le scritture."""
    import keyring
    import keyring.errors
    voci, guasto = {}, set()

    def get(s, u):
        return voci.get((s, u))

    def set_(s, u, v):
        if "set" in guasto:
            raise keyring.errors.PasswordSetError("portachiavi bloccato")
        voci[(s, u)] = v

    def delete(s, u):
        if (s, u) not in voci:
            raise keyring.errors.PasswordDeleteError("assente")
        del voci[(s, u)]
    monkeypatch.setattr(keyring, "get_password", get)
    monkeypatch.setattr(keyring, "set_password", set_)
    monkeypatch.setattr(keyring, "delete_password", delete)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/finto")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return voci, guasto


def test_chiave_spostata_dal_nome_vecchio(portachiavi):
    voci, _ = portachiavi
    voci[("spese-app", "anthropic")] = "sk-prova"
    assert llm.chiave_api() == "sk-prova"
    assert voci == {("CashFlowAnalyzer", "anthropic"): "sk-prova"}


def test_chiave_resta_se_la_scrittura_non_riesce(portachiavi):
    voci, guasto = portachiavi
    voci[("spese-app", "anthropic")] = "sk-prova"
    guasto.add("set")
    assert llm.chiave_api() == "sk-prova"                               # si usa lo stesso
    assert voci == {("spese-app", "anthropic"): "sk-prova"}             # e resta dov'era
    guasto.clear()
    assert llm.chiave_api() == "sk-prova" and voci == {("CashFlowAnalyzer", "anthropic"): "sk-prova"}


def test_salvare_una_chiave_toglie_quella_vecchia(portachiavi):
    voci, _ = portachiavi
    voci[("spese-app", "anthropic")] = "sk-vecchia"
    voci[("CashFlowAnalyzer", "anthropic")] = "sk-prima"
    llm.salva_chiave_api("sk-nuova")
    assert voci == {("CashFlowAnalyzer", "anthropic"): "sk-nuova"}

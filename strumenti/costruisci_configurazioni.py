#!/usr/bin/env python3
"""
costruisci_configurazioni.py - Genera le configurazioni derivate da dati pubblici
e verifica che tutte le configurazioni usino solo sottocategorie della tassonomia.

Genera in config/:
  - osm_tag_categorie.yaml  tipo di negozio OpenStreetMap -> sottocategoria
  - marchi_nsi.json         marchi commerciali (Name Suggestion Index) -> sottocategoria
  - mcc_categorie.csv       codici MCC delle carte (ISO 18245) -> sottocategoria

Sorgenti in sorgenti/:
  - mcc_codes.csv             github.com/greggles/mcc-codes (pubblico dominio)
  - nsi_marchi_spendifai.json regole dei marchi generate da Spendif.ai a partire dal
                              Name Suggestion Index di OpenStreetMap (dati NSI: licenza ISC)

Uso:  python3 strumenti/costruisci_configurazioni.py      (dalla cartella del progetto)
Requisiti: PyYAML (su Linux Mint: sudo apt install python3-yaml)
"""
import csv
import json
import re
import sys
from pathlib import Path

import yaml

RADICE = Path(__file__).resolve().parent.parent
CONFIG = RADICE / "config"
SORGENTI = RADICE / "sorgenti"

# ---------------------------------------------------------------------------
# Tipo di negozio OpenStreetMap -> sottocategoria. None = escluso.
# ---------------------------------------------------------------------------
OSM = {
    "shop=supermarket": "Supermercato",
    "shop=convenience": "Supermercato",
    "shop=bakery": "Negozi alimentari",
    "shop=butcher": "Negozi alimentari",
    "shop=greengrocer": "Negozi alimentari",
    "shop=alcohol": "Negozi alimentari",
    "amenity=fast_food": "Ristoranti",
    "amenity=restaurant": "Ristoranti",
    "amenity=pub": "Ristoranti",
    "amenity=cafe": "Bar e pasticcerie",
    "amenity=bar": "Bar e pasticcerie",
    "amenity=fuel": "Carburante e ricarica",
    "amenity=charging_station": "Carburante e ricarica",
    "amenity=parking": "Parcheggi",
    "amenity=car_wash": "Auto: manutenzione",
    "amenity=cinema": "Cinema",
    "amenity=theatre": "Spettacoli e concerti",
    "amenity=gym": "Sport e palestra",
    "leisure=fitness_centre": "Sport e palestra",
    "leisure=sports_centre": "Sport e palestra",
    "leisure=golf_course": "Sport e palestra",
    "shop=sports": "Sport e palestra",
    "shop=books": "Libri e media",
    "shop=stationery": "Scuola e formazione",
    "shop=clothing": "Abbigliamento e calzature",
    "shop=shoes": "Abbigliamento e calzature",
    "shop=jewelry": "Varie",
    "shop=chemist": "Cura personale",
    "shop=cosmetics": "Cura personale",
    "shop=electronics": "Elettronica",
    "shop=computer": "Elettronica",
    "shop=mobile_phone": "Internet e telefonia",
    "shop=furniture": "Arredo ed elettrodomestici",
    "shop=hardware": "Manutenzione e servizi",
    "shop=doityourself": "Manutenzione e servizi",
    "shop=florist": "Regali e donazioni",
    "shop=gift": "Regali e donazioni",
    "shop=pet": "Animali",
    "amenity=veterinary": "Animali",
    "amenity=post_office": "Varie",
    "tourism=hotel": "Alloggi",
    "tourism=hostel": "Alloggi",
    "tourism=motel": "Alloggi",
    "office=travel_agent": "Pacchetti/agenzie",
    "office=insurance": "Assicurazioni (altre)",
    "amenity=pharmacy": "Farmacia",
    "amenity=hospital": "Visite ed esami",
    "amenity=clinic": "Visite ed esami",
    "amenity=dentist": "Dentista",
    "shop=optician": "Ottica",
    # esclusi: nomi di banche e servizi finanziari compaiono nelle causali e creerebbero falsi positivi
    "amenity=bank": None,
    "amenity=atm": None,
    "office=financial": None,
}

# ---------------------------------------------------------------------------
# Codici MCC -> sottocategoria. Intervalli e codici singoli; generico=True per i codici
# troppo vaghi per decidere da soli (la catena prosegue con marchi, storico e modello).
# ---------------------------------------------------------------------------
MCC_INTERVALLI = [
    (3000, 3350, "Voli", False),
    (3351, 3500, "Noleggio auto", False),
    (3501, 3999, "Alloggi", False),
    (5611, 5699, "Abbigliamento e calzature", False),
    (5712, 5719, "Arredo ed elettrodomestici", False),
    (5961, 5969, "Online da dettagliare", True),
    (8011, 8099, "Visite ed esami", False),
    (8211, 8299, "Scuola e formazione", False),
]
MCC_CODICI = {
    # trasporti e viaggi
    4011: ("Mezzi pubblici", True), 4111: ("Mezzi pubblici", False), 4112: ("Treni lunga percorrenza", False),
    4121: ("Taxi e car sharing", False), 4131: ("Mezzi pubblici", False), 4411: ("Pacchetti/agenzie", False),
    4457: ("Hobby", False), 4468: ("Hobby", False), 4511: ("Voli", False), 4582: ("Voli", False),
    4722: ("Pacchetti/agenzie", False), 4784: ("Pedaggi e Telepass", False), 4789: ("Mezzi pubblici", True),
    5511: ("Auto: noleggio/rata", True), 5521: ("Auto: noleggio/rata", True), 5531: ("Auto: manutenzione", False),
    5532: ("Auto: manutenzione", False), 5533: ("Auto: manutenzione", False), 5541: ("Carburante e ricarica", False),
    5542: ("Carburante e ricarica", False), 5552: ("Carburante e ricarica", False), 5571: ("Auto: manutenzione", False),
    5598: ("Hobby", False), 5599: ("Auto: manutenzione", True),
    7011: ("Alloggi", False), 7012: ("Alloggi", False), 7032: ("Alloggi", False), 7033: ("Alloggi", False),
    7512: ("Taxi e car sharing", False), 7513: ("Noleggio auto", False), 7519: ("Noleggio auto", False),
    7523: ("Parcheggi", False), 7531: ("Auto: manutenzione", False), 7534: ("Auto: manutenzione", False),
    7535: ("Auto: manutenzione", False), 7538: ("Auto: manutenzione", False), 7542: ("Auto: manutenzione", False),
    7549: ("Auto: manutenzione", False), 8675: ("Auto: manutenzione", True),
    # telecomunicazioni, utenze, digitale
    4812: ("Elettronica", False), 4814: ("Internet e telefonia", False), 4816: ("Abbonamenti digitali", True),
    4899: ("Abbonamenti digitali", False), 4900: ("Energia e gas", False), 5983: ("Energia e gas", False),
    5815: ("Libri e media", False), 5816: ("Hobby", False), 5817: ("Abbonamenti digitali", False),
    5818: ("Abbonamenti digitali", True), 7372: ("Abbonamenti digitali", True), 7375: ("Abbonamenti digitali", False),
    # casa
    5200: ("Manutenzione e servizi", False), 5211: ("Manutenzione e servizi", False), 5231: ("Manutenzione e servizi", False),
    5251: ("Manutenzione e servizi", False), 5261: ("Manutenzione e servizi", False), 5722: ("Arredo ed elettrodomestici", False),
    5950: ("Arredo ed elettrodomestici", False), 5996: ("Manutenzione e servizi", False), 7210: ("Manutenzione e servizi", False),
    7211: ("Manutenzione e servizi", False), 7216: ("Manutenzione e servizi", False), 7217: ("Manutenzione e servizi", False),
    7342: ("Manutenzione e servizi", False), 7349: ("Manutenzione e servizi", False), 7623: ("Manutenzione e servizi", False),
    7641: ("Manutenzione e servizi", False), 7692: ("Manutenzione e servizi", True), 7699: ("Manutenzione e servizi", True),
    6513: ("Mutuo/Affitto", True),
    # alimentari e ristorazione
    5300: ("Supermercato", False), 5411: ("Supermercato", False), 5422: ("Negozi alimentari", False),
    5441: ("Negozi alimentari", False), 5451: ("Negozi alimentari", False), 5462: ("Negozi alimentari", False),
    5499: ("Negozi alimentari", False), 5921: ("Negozi alimentari", False),
    5811: ("Ristoranti", False), 5812: ("Ristoranti", False), 5813: ("Bar e pasticcerie", False), 5814: ("Ristoranti", False),
    # acquisti vari
    5309: ("Varie", True), 5310: ("Varie", True), 5311: ("Varie", True), 5331: ("Varie", True), 5399: ("Varie", True),
    5732: ("Elettronica", False), 5734: ("Elettronica", True), 5946: ("Elettronica", False), 7379: ("Elettronica", False),
    7622: ("Elettronica", False), 5931: ("Varie", True), 5932: ("Varie", True), 5937: ("Varie", True),
    5944: ("Varie", False), 7631: ("Varie", False), 5947: ("Regali e donazioni", False), 5992: ("Regali e donazioni", False),
    5993: ("Varie", False), 5999: ("Varie", True), 7299: ("Varie", True), 7338: ("Varie", True), 7399: ("Varie", True),
    8111: ("Varie", True), 8911: ("Varie", True), 8931: ("Varie", True), 8999: ("Varie", True), 9402: ("Varie", False),
    8398: ("Regali e donazioni", False), 8641: ("Regali e donazioni", False), 8661: ("Regali e donazioni", False),
    # svago
    5733: ("Hobby", False), 5735: ("Libri e media", False), 5942: ("Libri e media", False), 5994: ("Libri e media", False),
    5940: ("Sport e palestra", False), 5941: ("Sport e palestra", False), 5945: ("Hobby", False), 5949: ("Hobby", False),
    5970: ("Hobby", False), 5971: ("Hobby", False), 5972: ("Hobby", False), 7221: ("Hobby", False),
    7832: ("Cinema", False), 7841: ("Abbonamenti digitali", False), 7911: ("Spettacoli e concerti", False),
    7922: ("Spettacoli e concerti", False), 7929: ("Spettacoli e concerti", False), 7932: ("Hobby", False),
    7933: ("Hobby", False), 7941: ("Sport e palestra", False), 7991: ("Musei e mostre", False),
    7992: ("Sport e palestra", False), 7993: ("Hobby", False), 7994: ("Hobby", False), 7996: ("Hobby", False),
    7997: ("Sport e palestra", False), 7998: ("Musei e mostre", False), 7999: ("Hobby", True), 8699: ("Hobby", True),
    7800: ("Hobby", False), 7801: ("Hobby", False), 7802: ("Hobby", False), 7995: ("Hobby", False),
    # persona
    5948: ("Abbigliamento e calzature", False), 7251: ("Abbigliamento e calzature", False), 7296: ("Abbigliamento e calzature", False),
    5977: ("Cura personale", False), 7230: ("Cura personale", False), 7297: ("Cura personale", False), 7298: ("Cura personale", False),
    # famiglia e istruzione
    5943: ("Scuola e formazione", False), 8351: ("Figli", False), 5995: ("Animali", False), 742: ("Animali", False),
    # salute (solo locale)
    5912: ("Farmacia", False), 5975: ("Visite ed esami", False), 5976: ("Visite ed esami", False),
    8021: ("Dentista", False), 8042: ("Ottica", False), 8043: ("Ottica", False), 7277: ("Visite ed esami", False),
    # finanza, PA, contanti
    6010: ("Prelievo da giustificare", False), 6011: ("Prelievo da giustificare", False),
    6012: ("Commissioni bancarie", True), 6300: ("Assicurazioni (altre)", False), 5960: ("Assicurazioni (altre)", False),
    6211: ("Accantonamento", True),
    7276: ("Imposte", False), 9211: ("Multe", False), 9222: ("Multe", False), 9311: ("Imposte", False),
    9399: ("Tributi locali", True),
}


def carica_tassonomia():
    t = yaml.safe_load((CONFIG / "tassonomia.yaml").read_text(encoding="utf-8"))
    l2 = {}
    for sezione in ("uscite", "entrate", "risparmio"):
        for l1, voci in t[sezione].items():
            for v in voci:
                # la stessa sottocategoria puo' stare sotto categorie diverse (CASA · Varie, ACQUISTI · Varie):
                # le configurazioni che la citano per nome usano la prima, salvo che indichino la categoria
                l2.setdefault(v, l1)
    return l2, set(t.get("solo_locale", []))


def verifica(l2_validi, l2, origine):
    if l2 is not None and l2 not in l2_validi:
        sys.exit("Sottocategoria sconosciuta '%s' in %s" % (l2, origine))


def main():
    l2_validi, solo_locale = carica_tassonomia()

    # 1. tipi di negozio OSM
    for tag, l2 in OSM.items():
        verifica(l2_validi, l2, "OSM " + tag)
    righe = ["# Generato da strumenti/costruisci_configurazioni.py: modificare li', non qui.",
             "# Tipo di negozio OpenStreetMap -> sottocategoria (null = escluso).", "versione: 1", "tag:"]
    for tag, l2 in OSM.items():
        righe.append("  %s: %s" % (tag, "null" if l2 is None else '"%s"' % l2))
    (CONFIG / "osm_tag_categorie.yaml").write_text("\n".join(righe) + "\n", encoding="utf-8")

    # 2. marchi NSI
    nsi = json.loads((SORGENTI / "nsi_marchi_spendifai.json").read_text(encoding="utf-8"))
    marchi, esclusi, sconosciuti = [], 0, 0
    for r in nsi["rules"]:
        tag = r.get("osm_tag")
        if tag not in OSM:
            sconosciuti += 1
            continue
        if OSM[tag] is None:
            esclusi += 1
            continue
        paesi = [p.upper() for p in (r.get("countries") or [])]
        priorita = 0 if "IT" in paesi else 1 if not paesi else 2
        # Il pattern si ricostruisce dal nome intero del marchio (parole intere): i pattern NSI
        # originali includono abbreviazioni come "supermerca" che generano falsi positivi.
        nome = re.sub(r"\s*\(.*?\)\s*", " ", r.get("brand") or "").strip()
        parole = re.findall(r"[^\W_]+", nome)
        if not parole or len("".join(parole)) < 4:
            continue
        pattern = r"(?<![^\W_])" + r"[\W_]*".join(re.escape(w) for w in parole) + r"(?![^\W_])"
        # Decisivo solo se il marchio e' presente in Italia e il nome non e' una parola corta e comune
        decisivo = priorita == 0 and (len(parole) > 1 or len(parole[0]) >= 6)
        marchi.append({"pattern": pattern, "marchio": nome, "osm_tag": tag, "l2": OSM[tag],
                       "l1": l2_validi[OSM[tag]], "priorita": priorita, "decisivo": decisivo})
    marchi.sort(key=lambda m: (m["priorita"], -len(m["pattern"])))
    uscita = {"versione": 1,
              "fonte": "Name Suggestion Index di OpenStreetMap (licenza ISC), regole generate da Spendif.ai",
              "uso": "su nome esercente gia' ripulito, parole intere; vince la priorita' piu' bassa (0 = marchio presente "
                     "in Italia, 1 = senza paese, 2 = altri paesi), poi la corrispondenza piu' lunga. Solo i marchi "
                     "con decisivo=true assegnano la categoria; gli altri sono una proposta da confermare",
              "marchi": marchi}
    (CONFIG / "marchi_nsi.json").write_text(json.dumps(uscita, ensure_ascii=False, indent=0), encoding="utf-8")

    # 3. codici MCC
    out = []
    with open(SORGENTI / "mcc_codes.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            codice = int(r["mcc"])
            l2, generico = None, True
            for a, b, x, g in MCC_INTERVALLI:
                if a <= codice <= b:
                    l2, generico = x, g
            if codice in MCC_CODICI:
                l2, generico = MCC_CODICI[codice]
            verifica(l2_validi, l2, "MCC %04d" % codice)
            out.append({"mcc": "%04d" % codice, "descrizione_en": r["edited_description"],
                        "l1": l2_validi.get(l2, "") if l2 else "", "l2": l2 or "",
                        "generico": "si" if (generico or not l2) else "no"})
    with open(CONFIG / "mcc_categorie.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["mcc", "descrizione_en", "l1", "l2", "generico"])
        w.writeheader()
        w.writerows(out)

    # 4. verifica delle configurazioni scritte a mano
    regole = yaml.safe_load((CONFIG / "regole_esercenti_it.yaml").read_text(encoding="utf-8"))["regole"]
    for r in regole:
        verifica(l2_validi, r["l2"], "regole_esercenti_it.yaml: " + r["pattern"])
        if r.get("forza", "alta") not in ("alta", "bassa"):
            sys.exit("forza non valida in regole_esercenti_it.yaml: " + r["pattern"])
        re.compile(r["pattern"])
    yaml.safe_load((CONFIG / "intermediari.yaml").read_text(encoding="utf-8"))
    yaml.safe_load((CONFIG / "sinonimi_colonne.yaml").read_text(encoding="utf-8"))
    yaml.safe_load((CONFIG / "impostazioni.yaml").read_text(encoding="utf-8"))

    mappati = sum(1 for r in out if r["l2"])
    decisi = sum(1 for r in out if r["generico"] == "no")
    print("osm_tag_categorie.yaml: %d tipi di negozio (%d esclusi)" % (len(OSM), sum(1 for v in OSM.values() if v is None)))
    print("marchi_nsi.json: %d marchi, %d decisivi (%d esclusi perche' banche/finanza)" % (
        len(marchi), sum(1 for m in marchi if m["decisivo"]), esclusi))
    print("mcc_categorie.csv: %d codici, %d con categoria, %d decisivi da soli" % (len(out), mappati, decisi))
    print("regole_esercenti_it.yaml: %d regole valide" % len(regole))
    print("Tassonomia: %d sottocategorie; solo locale: %s" % (len(l2_validi), ", ".join(sorted(solo_locale))))


if __name__ == "__main__":
    main()

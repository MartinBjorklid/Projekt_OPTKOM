import hashlib
import io
import os

import numpy as np
import streamlit as st
from PIL import Image, ImageFile

from Signalbehandling import decode, huffman_decode, tree

# ==============================================================================
# VISNING AV SKICKADE/MOTTAGNA FILER I GRÄNSSNITTET
# ==============================================================================

BILDFORMAT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".avif"}
TEXTFORMAT = {".txt", ".csv", ".md", ".py", ".json", ".log"}
VISNINGSSTORLEK = 256  # Små bilder förstoras till ungefär så här många pixlar


def bitar_till_bytes(bitar):
    """Gör om en lista med 0/1 till bytes. Ofullständiga byte i slutet ignoreras."""
    n = len(bitar) // 8 * 8
    return np.packbits(np.asarray(bitar[:n], dtype=np.uint8)).tobytes()


def dela_fildata(raw):
    """Delar upp 'filnamn + 0x00 + innehåll'. Returnerar (filnamn, innehåll) eller (None, None)."""
    sep = raw.find(b"\x00")
    if sep == -1:
        return None, None
    return raw[:sep].decode("utf-8", errors="ignore"), raw[sep + 1:]


def delvis_avkodning(bitar, H):
    """
    Avkodar de bitar som hittills tagits emot, för livevisning.
    Returnerar {"typ": "text", "text": ...} eller {"typ": "fil", "filnamn": ..., "data": ...}.
    """
    n = len(bitar) // 7 * 7  # Bara hela Hamming-block
    if n == 0:
        return None
    avkodat = decode(bitar[:n], H, 0, verbose=False)
    flagga, nytta = avkodat[0], avkodat[1:]
    if flagga == 0:
        return {"typ": "text", "text": huffman_decode(nytta, tree, allow_incomplete=True)}
    filnamn, data = dela_fildata(bitar_till_bytes(nytta))
    return {"typ": "fil", "filnamn": filnamn, "data": data}


def las_bild(data, ofullstandig=False):
    """Öppnar en bild i sitt eget färgläge (t.ex. palett). ofullstandig=True tillåter en halvt mottagen fil. None om det inte går."""
    if not data:
        return None
    tidigare = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = ofullstandig
    try:
        bild = Image.open(io.BytesIO(data))
        bild.load()
        return bild
    except Exception:
        return None
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = tidigare


def oppna_bild(data, ofullstandig=False):
    """Öppnar en bild som RGBA. None om det inte går."""
    bild = las_bild(data, ofullstandig)
    return bild.convert("RGBA") if bild is not None else None


def forstora(bild):
    """Förstorar små bilder utan utjämning så att enskilda pixlar syns tydligt."""
    faktor = max(1, VISNINGSSTORLEK // max(bild.size))
    return bild.resize((bild.width * faktor, bild.height * faktor), Image.NEAREST)


def hexdump(data, max_bytes=256):
    rader = []
    for i in range(0, min(len(data), max_bytes), 16):
        del_data = data[i:i + 16]
        hex_del = " ".join(f"{b:02x}" for b in del_data)
        ascii_del = "".join(chr(b) if 32 <= b < 127 else "." for b in del_data)
        rader.append(f"{i:04x}  {hex_del:<47}  {ascii_del}")
    if len(data) > max_bytes:
        rader.append(f"... ({len(data)} bytes totalt)")
    return "\n".join(rader)


def visa_fil(filnamn, data, ofullstandig=False):
    """Visar en fil i gränssnittet: bild, text eller hexdump beroende på filtyp."""
    filandelse = os.path.splitext(filnamn or "")[1].lower()
    bild = oppna_bild(data, ofullstandig)

    if bild is not None:
        st.image(forstora(bild), caption=f"{filnamn} ({bild.width}×{bild.height} px)")
    elif filandelse in BILDFORMAT:
        if ofullstandig:
            st.caption("Bilden visas så snart tillräckligt mycket data har kommit fram.")
        else:
            st.warning("Bilden kunde inte avkodas, eftersom filen är skadad eller ofullständig. "
                       "Komprimerade format (PNG, JPEG, AVIF) tål inte bitfel. Ett okomprimerat format som BMP gör det.")
            st.code(hexdump(data), language=None)
    elif filandelse in TEXTFORMAT:
        st.code(data[:2000].decode("utf-8", errors="replace"), language=None)
    else:
        st.code(hexdump(data), language=None)


def jamfor_filer(skickad, mottagen):
    """Jämför skickad och mottagen fil byte för byte."""
    n = min(len(skickad), len(mottagen))
    a = np.frombuffer(skickad[:n], dtype=np.uint8)
    b = np.frombuffer(mottagen[:n], dtype=np.uint8)
    return {
        "identisk": skickad == mottagen,
        "skickade_bytes": len(skickad),
        "mottagna_bytes": len(mottagen),
        "fel_bytes": int(np.count_nonzero(a != b)) + abs(len(skickad) - len(mottagen)),
        "fel_bitar": int(np.unpackbits(a ^ b).sum()),
        "sha_skickad": hashlib.sha256(skickad).hexdigest(),
        "sha_mottagen": hashlib.sha256(mottagen).hexdigest(),
    }


def visa_kvitto(jamforelse, antal_rattade):
    """Kvitto som visar om filen kom fram identisk."""
    j = jamforelse
    if j["identisk"]:
        st.success(f"✅ **Identisk fil**: {j['skickade_bytes']} av {j['skickade_bytes']} bytes korrekta")
    elif j["skickade_bytes"] != j["mottagna_bytes"]:
        st.error(f"❌ **Fel längd**: skickade {j['skickade_bytes']} bytes, tog emot {j['mottagna_bytes']} bytes "
                 f"({j['fel_bytes']} bytes skiljer sig)")
    else:
        st.error(f"❌ **{j['fel_bytes']} av {j['skickade_bytes']} bytes skiljer sig** "
                 f"({j['fel_bitar']} bitfel kvar efter Hamming-rättning)")

    st.code(f"SHA-256 skickad:  {j['sha_skickad']}\nSHA-256 mottagen: {j['sha_mottagen']}", language=None)

    if antal_rattade is not None:
        st.info(f"Hamming-koden rättade **{antal_rattade}** bitfel under överföringen.")


BMP_HUVUD = 54  # Filhuvud + informationshuvud i en BMP (före färgpaletten)


def skillnadsbild(skickad, mottagen):
    """
    Mottagen bild nedtonad, med pixlar som skiljer sig från originalet markerade i rött
    och pixlar vars färg bara ändrats via paletten markerade i gult.
    Returnerar (bild, antal_felaktiga_pixlar, lanat_huvud, antal_palettpixlar) eller
    (None, 0, False, 0) om bilderna inte går att jämföra. Om en BMP:s huvud skadats lånas
    originalets huvud, så att felen i pixeldatan ändå kan visas (lanat_huvud=True).

    Palettbilder (t.ex. 4-bitars BMP) jämförs på färgindex per pixel. Ett bitfel i själva
    paletten ändrar annars nyansen i alla pixlar med den färgen, ofta så lite att det inte
    syns, och då skulle hela ytor markeras som fel. De pixlarna markeras i stället i gult.
    """
    a = las_bild(skickad)
    b = las_bild(mottagen, ofullstandig=True)
    lanat_huvud = False
    huvud_skadat = b is None or (a is not None and b.size != a.size)
    if huvud_skadat and skickad[:2] == b"BM" and len(skickad) == len(mottagen):
        b = las_bild(skickad[:BMP_HUVUD] + mottagen[BMP_HUVUD:], ofullstandig=True)
        lanat_huvud = b is not None
    if a is None or b is None or a.size != b.size:
        return None, 0, False, 0

    palettfel = np.zeros(a.size[::-1], dtype=bool)
    if a.mode == "P" and b.mode == "P":
        index_a = np.asarray(a)
        fel = index_a != np.asarray(b)
        # Vilka palettfärger som skadats (en saknad färg räknas också som skadad)
        pal_a = np.array(a.getpalette() or [], dtype=np.int16).reshape(-1, 3)
        pal_b = np.array(b.getpalette() or [], dtype=np.int16).reshape(-1, 3)
        andrad_farg = np.ones(256, dtype=bool)
        n = min(len(pal_a), len(pal_b))
        andrad_farg[:n] = np.any(pal_a[:n] != pal_b[:n], axis=1)
        # Rätt färgindex men skadad palettfärg -> gult
        palettfel = ~fel & andrad_farg[index_a]
    else:
        fel = np.any(np.asarray(a.convert("RGBA"), dtype=np.int16) !=
                     np.asarray(b.convert("RGBA"), dtype=np.int16), axis=2)

    B = np.asarray(b.convert("RGBA"), dtype=np.int16)
    gra = B[..., :3].mean(axis=2, keepdims=True) * 0.35 + 165
    ut = np.repeat(gra, 3, axis=2)
    ut[palettfel] = [245, 190, 0]
    ut[fel] = [230, 30, 30]
    return Image.fromarray(ut.astype(np.uint8), "RGB"), int(fel.sum()), lanat_huvud, int(palettfel.sum())

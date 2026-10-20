import altair as alt
import numpy as np
import pandas as pd

# ==============================================================================
# GRAFER SOM INTE LAGGAR VID STORA DATAMÄNGDER
# ==============================================================================

MAX_PUNKTER = 4000  # Ungefär så många punkter skickas till webbläsaren per graf


def minmax_utglesning(df, kolumn, max_punkter=MAX_PUNKTER):
    """
    Glesar ut en signal genom att behålla lägsta och högsta värdet i varje segment.
    Till skillnad från att ta var n:te punkt försvinner då inga bitflanker eller spikar.
    """
    n = len(df)
    if n <= max_punkter:
        return df
    segment = int(np.ceil(n / (max_punkter // 2)))
    y = df[kolumn].to_numpy()
    antal = n // segment
    block = y[:antal * segment].reshape(antal, segment)
    start = np.arange(antal) * segment
    index = [start + block.argmin(axis=1), start + block.argmax(axis=1)]
    if n % segment:
        rest_start = antal * segment
        rest = y[rest_start:]
        index += [[rest_start + rest.argmin()], [rest_start + rest.argmax()]]
    return df.iloc[np.unique(np.concatenate(index))]


RATTAD_FARG = "#3b82f6"  # Blå: bitfel som Hamming-koden rättade
KVAR_FARG = "#ef4444"    # Röd: bitfel som finns kvar efter Hamming-rättningen
RATTAD_TEXT = "Rättat av Hamming"
KVAR_TEXT = "Fel kvar"


def signalgraf(df, kolumn, farg, troskel=None, troskelfarg="#ffaa00", markeringar=None):
    """
    Linjegraf (tid på x-axeln) med tröskelvärdet som en streckad horisontell linje.
    markeringar: {"rattade": [tider], "kvar": [tider]} ritas som lodräta linjer där bitfelen uppstod.
    """
    data = df.reset_index()
    tid = data.columns[0]
    x = alt.X(field=tid, type="quantitative", title=tid)
    graf = alt.Chart(data).mark_line(color=farg).encode(
        x=x,
        y=alt.Y(field=kolumn, type="quantitative", title=kolumn),
    )
    if troskel is not None:
        linje = alt.Chart(pd.DataFrame({"y": [troskel]})).mark_rule(
            color=troskelfarg, strokeDash=[6, 4]
        ).encode(y="y:Q")
        graf = graf + linje
    if markeringar and (markeringar["rattade"] or markeringar["kvar"]):
        fel = pd.DataFrame({
            tid: markeringar["rattade"] + markeringar["kvar"],
            "Bitfel": [RATTAD_TEXT] * len(markeringar["rattade"]) + [KVAR_TEXT] * len(markeringar["kvar"]),
        })
        # Pilar ovanför signalen gör felen lätta att se även där signalen är tät
        fel["y"] = float(data[kolumn].max()) + 0.6
        farg_skala = alt.Color("Bitfel:N", scale=alt.Scale(domain=[RATTAD_TEXT, KVAR_TEXT],
                                                            range=[RATTAD_FARG, KVAR_FARG]),
                               legend=alt.Legend(orient="bottom", title=None))
        streck = alt.Chart(fel).mark_rule(strokeWidth=1.5, opacity=0.85).encode(x=x, color=farg_skala)
        pilar = alt.Chart(fel).mark_point(shape="triangle-down", size=70, filled=True, opacity=1).encode(
            x=x, y=alt.Y("y:Q"), color=farg_skala
        )
        graf = graf + streck + pilar
    return graf.interactive(bind_y=False)


def bitstrang(bitar, max_antal=500):
    """Bitar som en kompakt sträng. Långa listor kortas av, eftersom tusentals bitar på en rad är segt att visa."""
    text = "".join(str(b) for b in bitar[:max_antal])
    if len(bitar) > max_antal:
        text += f" ... ({len(bitar)} bitar totalt, alla finns i textloggen)"
    return text

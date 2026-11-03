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


FEL_FARG = "#ef4444"  # Röd: bit som avlästes fel


def signalgraf(df, kolumn, farg, troskel=None, troskelfarg="#ffaa00", markeringar=None):
    """
    Linjegraf (tid på x-axeln) med tröskelvärdet som en streckad horisontell linje.
    markeringar: tider för bitfel, ritas som röda lodräta linjer där felen uppstod.
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
    if markeringar:
        fel = pd.DataFrame({tid: list(markeringar)})
        # Pilar ovanför signalen gör felen lätta att se även där signalen är tät
        fel["y"] = float(data[kolumn].max()) + 0.6
        streck = alt.Chart(fel).mark_rule(strokeWidth=1.5, opacity=0.85, color=FEL_FARG).encode(x=x)
        pilar = alt.Chart(fel).mark_point(shape="triangle-down", size=70, filled=True, opacity=1,
                                          color=FEL_FARG).encode(x=x, y=alt.Y("y:Q"))
        graf = graf + streck + pilar
    return graf.interactive(bind_y=False)


def bitstrang(bitar, max_antal=500):
    """Bitar som en kompakt sträng. Långa listor kortas av, eftersom tusentals bitar på en rad är segt att visa."""
    text = "".join(str(b) for b in bitar[:max_antal])
    if len(bitar) > max_antal:
        text += f" ... ({len(bitar)} bitar totalt, alla finns i textloggen)"
    return text

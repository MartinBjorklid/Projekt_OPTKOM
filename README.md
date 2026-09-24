# Projekt_OPTKOM UNSTABLE-BRANCH

>[!NOTE]
>Odsen att denna kod funkar utan felsökning är typ noll och därför ligger den här koden här...

## Installation
1. Installera python
  - Gå in på https://www.python.org/ och installera python
  
  >[!IMPORTANT]
  >Se till att klicka i att "Add python to PATH" under installationen, annars kommer saker inte att funka!
  
2. Installera följande python-paket med hjälp av pip
  * streamlit
  * numpy
  * nidaqmx
  * matplotlib
  
  Detta görs via körning av följande kommando: ```pip install [paket]```.
  
  >[!TIP]
  >Om det inte funkar med ```pip install [paket]``` så får du använda ```python3 -m pip install [paket]``` och [paket] byts självklart ut mot det faktiska paketet som du ska installera, t.ex ```numpy```

## Körning av program
När du har installerat python och paketen så startar du programmet vars interface öppnas i din webbläsare genom att köra:
```streamlit run /[path to folder]/optisk_kommunikation_app.py```

>[!TIP]
>Om detta inte funkar testa med ```python3 -m streamlit run /[path to folder]/optisk_kommunikation_app.py```

Programmets användargränssnitt öppnas automatiskt i din webbläsare.

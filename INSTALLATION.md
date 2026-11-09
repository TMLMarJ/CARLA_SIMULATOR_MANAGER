# Instalacja CARLA Simulator Manager

## Wymagania środowiskowe

- Windows z Pythonem zgodnym z CARLA 0.9.16;
- CARLA 0.9.16;
- Tkinter dostępny w instalacji Pythona;
- lokalny dostęp do modułu `carla` oraz oficjalnego katalogu `PythonAPI/carla/agents`.

Projekt nie instaluje ani nie uruchamia serwera CARLA automatycznie.

## Środowisko Python

Z katalogu projektu utwórz i aktywuj środowisko wirtualne:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.txt` deklaruje wersjonowane zależności PyYAML i Pillow. Kod używa również pakietów dostarczanych przez środowisko CARLA lub instalowanych osobno: `carla`, NumPy, Matplotlib, Pygame i Pygments. Projekt nie określa ich wersji, dlatego należy dobrać je do używanej dystrybucji CARLA i interpretera zamiast instalować przypadkowe wersje.

## CARLA Python API

Moduł `carla` musi być importowalny w wybranym interpreterze. Dodatkowo planer tras wyszukuje oficjalny moduł:

```text
agents/navigation/global_route_planner.py
```

Ustaw jedną ze zmiennych środowiskowych przed uruchomieniem aplikacji:

```powershell
$env:CARLA_PYTHONAPI_PATH = 'C:\CARLA_0.9.16\PythonAPI\carla'
```

albo:

```powershell
$env:CARLA_ROOT = 'C:\CARLA_0.9.16'
```

Pierwsza zmienna może wskazywać bezpośrednio katalog zawierający `agents/`. Druga wskazuje katalog instalacji CARLA; aplikacja sprawdza również wariant `PythonAPI/carla`.

## Uruchomienie

1. Uruchom serwer CARLA zgodnie z instrukcją używanej dystrybucji.
2. Uruchom GUI z katalogu projektu:

```powershell
python app.py
```

Domyślny endpoint w GUI to `127.0.0.1:2000`. Aplikację uruchamia się wyłącznie przez `python app.py`.

## Testy bez serwera

```powershell
python -m unittest discover -s tests -t .
```

Testy automatyczne używają fake'ów i mocków. Nie potwierdzają zgodności sieciowej, renderowania GPU ani zachowania prawdziwych okien Tkinter/Pygame.

## Typowe problemy

- **`ModuleNotFoundError: carla`** — moduł CARLA nie jest dostępny w aktywnym interpreterze.
- **Brak GlobalRoutePlanner** — ustaw poprawnie `CARLA_PYTHONAPI_PATH` lub `CARLA_ROOT` i uruchom aplikację ponownie.
- **Konflikt modułu `agents`** — usuń z `sys.path` inną paczkę o tej nazwie; resolver odrzuca planer załadowany spoza skonfigurowanego PythonAPI.
- **Brak Tkinter/Pygame** — użyj interpretera z Tkinter i doinstaluj zależności podglądów zgodne z CARLA.
- **Timeout połączenia** — sprawdź, czy serwer działa, port jest dostępny i GUI wskazuje właściwy host.
- **Brak starego cache map** — aplikacja czyta także dotychczasowy katalog `images/maps/generated/`, a nowe obrazy zapisuje w `resources/maps/generated/`.

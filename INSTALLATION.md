# Instalacja CARLA Simulator Manager

Instrukcja pierwszego uruchomienia dla początkującego użytkownika, obejmująca
pobranie serwera i projektu, znajduje się w [README](README.md). Poniższy
dokument rozwija konfigurację środowiska i CARLA Python API. Procedura Linux i
WSL2 znajduje się w [docs/LINUX_SETUP.md](docs/LINUX_SETUP.md), a zakres
weryfikacji w [audycie](docs/LINUX_COMPATIBILITY_AUDIT.md).

## Wymagania środowiskowe

- Windows 11 albo Linux x86-64 z Pythonem zgodnym z używanym wheel CARLA API;
- CARLA 0.9.15 albo 0.9.16; wersją referencyjną pozostaje 0.9.16;
- Tkinter dostępny w instalacji Pythona;
- importowalny moduł `carla` zgodny z serwerem oraz oficjalny katalog
  `PythonAPI/carla/agents` z tej samej instalacji.

Projekt nie instaluje ani nie uruchamia serwera CARLA automatycznie.

Zalecane pary środowisk to Python 3.10 dla CARLA 0.9.15 oraz Python 3.12 dla
CARLA 0.9.16. Inne interpretery należy dobierać wyłącznie wtedy, gdy istnieje
dla nich oficjalny wheel dokładnie tej wersji CARLA.

## Środowisko Python

`pyproject.toml` jest źródłem bezpośrednich zależności. `uv.lock` przechowuje
rozwiązanie dla Windows/Linux x86-64 oraz Pythona 3.10–3.12. Projekt nie jest
budowany jako biblioteka (`tool.uv.package = false`), a punkt wejścia pozostaje
`python app.py`.

### Zalecane: uv

Zainstaluj [uv](https://docs.astral.sh/uv/getting-started/installation/), wybierz
jedną wersję CARLA i wykonaj odpowiednie polecenie w katalogu projektu:

| Środowisko Windows | Polecenie |
|---|---|
| CARLA 0.9.16 / Python 3.12 | `uv sync --locked --python 3.12 --extra carla-0-9-16 --no-dev` |
| CARLA 0.9.15 / Python 3.10 | `uv sync --locked --python 3.10 --extra carla-0-9-15 --no-dev` |

Następnie aktywuj środowisko:

```powershell
.\.venv\Scripts\Activate.ps1
```

Profile `carla-0-9-15` i `carla-0-9-16` są jawnie skonfigurowane jako
konfliktowe. Nie używaj `--all-extras` i nie instaluj obu w jednym venv.

### Alternatywa: venv i pip

Pip korzysta z krótkiej listy zgodności `requirements.txt`, zsynchronizowanej
z `[project].dependencies`. CARLA trzeba dobrać jawnie:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install "carla==0.9.16"
```

Wariant 0.9.15:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install "carla==0.9.15"
```

`requirements.txt` nie jest osobnym źródłem wersji: przy zmianie bezpośrednich
zależności należy zaktualizować tę samą prostą listę również w
`pyproject.toml`. Zależności pośrednie wybiera pip; pełne wersje powtarzalnego
środowiska są zapisane w `uv.lock`.

Nie używaj Pythona 3.14 dla CARLA 0.9.16 — oficjalne wheel tej wersji obejmują
CPython 3.10–3.12. Dla CARLA 0.9.15 najprostsza wspierana konfiguracja projektu
to Python 3.10.

Wheel CARLA oraz oficjalny katalog `agents` zawsze muszą odpowiadać wersji
serwera. Profile uv instalują wheel, lecz nie dostarczają katalogu Agents.

## CARLA Python API

Binarny moduł `carla` musi być zainstalowany w aktywnym venv. Katalog
`PythonAPI/carla/agents` jest osobnym zestawem skryptów Python z oficjalnej
dystrybucji lub źródeł tego samego wydania CARLA. Resolver
`core/carla_pythonapi.py` ładuje z tej samej oficjalnej instalacji:

```text
agents/navigation/global_route_planner.py
agents/navigation/basic_agent.py
```

Zalecane jest ustawienie jednej ze zmiennych środowiskowych przed uruchomieniem aplikacji:

```powershell
$env:CARLA_PYTHONAPI_PATH = 'C:\CARLA_0.9.16\PythonAPI\carla'
```

albo:

```powershell
$env:CARLA_ROOT = 'C:\CARLA_0.9.16'
```

Pierwsza zmienna może wskazywać bezpośrednio katalog zawierający `agents/`. Druga wskazuje katalog instalacji CARLA; aplikacja sprawdza również wariant `PythonAPI/carla`. Jeśli żadna nie jest ustawiona, resolver próbuje użyć już importowalnego `agents.navigation`. Jawnie skonfigurowana ścieżka ma pierwszeństwo, a moduł załadowany spoza niej jest odrzucany jako konflikt.

`CARLA_PYTHONAPI_PATH` jest ustawieniem Managera: resolver dodaje wskazany katalog
do `sys.path` podczas ładowania Agents. Ogólna zmienna `PYTHONPATH` nie jest
wymagana i nie powinna zastępować instalacji binarnego modułu `carla`; może też
przypadkowo wprowadzić Agents z niezgodnej wersji.

Kontrola środowiska:

```powershell
python -c "import carla, importlib.metadata as m; print(m.version('carla')); print(carla.__file__)"
python -c "from core.carla_pythonapi import load_basic_agent; a,e=load_basic_agent(); print('BasicAgent OK' if a else e)"
```

Oczekiwany jest numer zgodny z serwerem oraz `BasicAgent OK`.

## Uruchomienie

1. Uruchom serwer CARLA zgodnie z instrukcją używanej dystrybucji.
2. Uruchom GUI z katalogu projektu:

```powershell
python app.py
```

Domyślny endpoint w GUI to `127.0.0.1:2000`. Aplikację uruchamia się wyłącznie przez `python app.py`.

Minimalna konfiguracja uruchomieniowa w PyCharm może zawierać tylko:

```text
PYTHONUNBUFFERED=1;CARLA_PYTHONAPI_PATH=<katalog PythonAPI/carla>
```

Nie trzeba ustawiać `CARLA_MANAGER_DESTINATION_EXECUTOR`: brak tej zmiennej
wybiera `basic_agent`. Dla serwera zdalnego w Connection Managerze należy
podać osiągalny adres IP lub nazwę DNS i port RPC serwera.

## Opcjonalne zmienne środowiskowe

| Zmienna | Domyślnie | Zastosowanie |
|---|---|---|
| `CARLA_PYTHONAPI_PATH` | auto-import | katalog oficjalnego `PythonAPI/carla` |
| `CARLA_ROOT` | brak | alternatywne wskazanie instalacji CARLA |
| `CARLA_MANAGER_DESTINATION_EXECUTOR` | `basic_agent` | diagnostyczny wybór `basic_agent`, `path` lub `route`; inna wartość kończy aktywację czytelnym błędem |
| `CARLA_MANAGER_BASIC_AGENT_TARGET_SPEED` | `40` | fallback km/h dla uruchomień bez ustawień GUI |
| `CARLA_MANAGER_BASIC_AGENT_ADAPTIVE_SPEED` | `1` | fallback adaptacji (`1/0`, `true/false`, `yes/no`, `on/off`) |
| `CARLA_MANAGER_NAV_DEBUG` | wyłączone | ograniczona diagnostyka Destination |
| `CARLA_MANAGER_TM_DEBUG` | wyłączone | diagnostyka lifecycle Traffic Managera |
| `CARLA_MANAGER_TM_PORT` | `8050` | zarezerwowany port TM aplikacji |
| `CARLA_MANAGER_TM_EXCLUSIVE` | włączone | kontrakt wyłącznego TM; tryb shared wymaga świadomej konfiguracji |

Wartości Maximum speed i Adaptive speed przesłane przez GUI mają pierwszeństwo
przed fallbackami środowiskowymi i dotyczą tylko konkretnego uruchomienia
Destination.

## Traffic Manager i wielu klientów

Domyślnie aplikacja używa własnego, ekskluzywnego portu TM `8050`, co pozwala
bezpiecznie zamknąć lokalny TM przed zmianą mapy. Port musi być wolny i
administracyjnie zarezerwowany dla danego procesu. Druga instancja aplikacji,
jeżeli ma używać TM, powinna otrzymać inny zarezerwowany port, np. `8051`.

Tryb shared nie daje aplikacji prawa do zatrzymywania TM. W takim trybie
zmiana świata może zostać celowo przerwana, jeżeli bezpieczny teardown nie
jest możliwy. Szczegóły opisuje
[CARLA_COMMUNICATION.md](docs/CARLA_COMMUNICATION.md).

## Testy bez serwera

```powershell
python -m unittest discover -s tests -t .
```

Testy automatyczne używają fake'ów i mocków. Nie potwierdzają zgodności sieciowej, renderowania GPU ani zachowania prawdziwych okien Tkinter/Pygame.

## Typowe problemy

- **`ModuleNotFoundError: carla`** — moduł CARLA nie jest dostępny w aktywnym interpreterze.
- **Brak GlobalRoutePlanner lub BasicAgent** — ustaw poprawnie `CARLA_PYTHONAPI_PATH` lub `CARLA_ROOT`, sprawdź zgodność katalogu `agents`, wheel i serwera, a następnie uruchom aplikację ponownie.
- **Konflikt modułu `agents`** — usuń z `sys.path` inną paczkę o tej nazwie; resolver odrzuca planer załadowany spoza skonfigurowanego PythonAPI.
- **Brak Tkinter/Pygame** — użyj interpretera z Tkinter i doinstaluj zależności podglądów zgodne z CARLA.
- **Timeout połączenia** — sprawdź, czy serwer działa, port jest dostępny i GUI wskazuje właściwy host.
- **Błąd portu Traffic Managera** — zarezerwuj osobny wolny port dla każdej instancji aplikacji korzystającej z ekskluzywnego TM.
- **Brak RSS** — aktualny build CARLA Python client nie udostępnia RSS; aplikacja informuje, że taki podgląd jest niedostępny.
- **Brak starego cache map** — aplikacja nadal czyta dotychczasowe katalogi
  `resources/maps/generated/` i `images/maps/generated/`. Nowe obrazy zapisuje
  w cache użytkownika: `%LOCALAPPDATA%\CARLA Simulator Manager\Cache` na
  Windows albo `$XDG_CACHE_HOME/carla-simulator-manager`/`~/.cache` na Linuxie.

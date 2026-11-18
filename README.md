# CARLA Simulator Manager

CARLA Simulator Manager to desktopowy klient GUI (Tkinter) do obsługi działającego
serwera CARLA. Pozwala zarządzać światem, aktorami, sensorami, ruchem i ustawieniami
symulacji bez pisania własnego skryptu Python.

CARLA działa w modelu klient–serwer: `CarlaUE4.exe`/`CarlaUE4.sh` uruchamia
symulację 3D, a Manager łączy się z nią przez CARLA Python API. Projekt nie
pobiera ani nie uruchamia serwera automatycznie.

## Najważniejsze funkcje

- połączenie lokalne i zdalne, Disconnect/Reconnect oraz Refresh World;
- mapy, pogoda, `WorldSettings`, tryby sync/async i Run/Pause/Step;
- Vehicles, Pedestrians, Static Objects, Sensors i wiele Ego Vehicles;
- ownership Managed/External/Internal oraz synchronizacja wielu klientów;
- Manual Control, Free Roam, Destination, Movement Preview i Spectator Preview;
- Dashboard z Delta Time i rzeczywistym Simulation FPS;
- Save/Load/Preflight/Replace/Clear w Configuration Management (JSON/YAML).

Configuration Management zapisuje obsługiwany stan środowiska, a nie pełny,
deterministyczny scenariusz testowy. Techniczne nazwy `ScenarioManager` i
`scenarios/` pozostają w kodzie dla kompatybilności.

## Platformy i wersje

| CARLA | Oficjalne wheel CPython | Zalecany Python |
|---|---|---|
| 0.9.15 | 3.7–3.10 | 3.10 |
| 0.9.16 | 3.10–3.12 | 3.12 |

Obsługiwane są Windows i Linux x86-64. Konfiguracją podstawową jest Windows 11 +
CARLA 0.9.16. Wstępnie sprawdzono także Ubuntu 26.04.1 LTS w WSL2/WSLg z
Pythonem 3.12.16 i klientem CARLA 0.9.16 łączącym się z serwerem na Windows.
Nie był to pełny E2E ani test natywnego Linuxa. Linux + CARLA 0.9.15 pozostaje
do weryfikacji. CARLA 1.0 nie jest obsługiwana.

Źródła pakietów: [CARLA releases](https://github.com/carla-simulator/carla/releases),
[PyPI 0.9.15](https://pypi.org/project/carla/0.9.15/) i
[PyPI 0.9.16](https://pypi.org/project/carla/0.9.16/).

## Wymagania

- kompatybilny serwer CARLA 0.9.15 lub 0.9.16;
- Python zgodny z wheel CARLA (nie zakładaj zgodności Pythona 3.14);
- Tkinter i aktywna sesja graficzna;
- `uv` (zalecane dla powtarzalnej instalacji) albo `pip`;
- zależności runtime zadeklarowane w `pyproject.toml`;
- moduł binarny `carla` oraz pasujący katalog `PythonAPI/carla/agents`.

## Pobranie projektu

```text
git clone https://github.com/TMLMarJ/CARLA_SIMULATOR_MANAGER.git
cd CARLA_SIMULATOR_MANAGER
```

Clone pobiera domyślną gałąź repozytorium — początkujący użytkownik nie musi jej
zmieniać. Jeśli świadomie testujesz inną opublikowaną gałąź, wykonaj po clone
`git fetch` i `git switch <nazwa-gałęzi>`. Alternatywnie pobierz **Code → Download
ZIP** i otwórz terminal w katalogu zawierającym `app.py`.

## Szybki start — Windows 11 i CARLA 0.9.16

1. Pobierz i rozpakuj CARLA 0.9.16, np. do `C:\CARLA_0.9.16`.
2. Zainstaluj [uv](https://docs.astral.sh/uv/getting-started/installation/), a
   następnie w PowerShell, w katalogu projektu, wykonaj:

```powershell
uv sync --locked --python 3.12 --extra carla-0-9-16 --no-dev
.\.venv\Scripts\Activate.ps1
$env:CARLA_PYTHONAPI_PATH = 'C:\CARLA_0.9.16\PythonAPI\carla'
python -c "import carla, importlib.metadata as m; print(m.version('carla')); print(carla.__file__)"
python -c "from core.carla_pythonapi import load_basic_agent; a,e=load_basic_agent(); print('BasicAgent OK' if a else e)"
```

3. W drugim PowerShell uruchom serwer:

```powershell
cd C:\CARLA_0.9.16
.\CarlaUE4.exe -quality-level=Low
```

4. W pierwszym PowerShell uruchom GUI:

```powershell
python app.py
```

Dla CARLA 0.9.15 użyj `uv sync --locked --python 3.10 --extra carla-0-9-15 --no-dev`
oraz katalogu Agents z 0.9.15. Profile są wzajemnie wykluczające się;
nie włączaj obu w jednym venv.
Pełna instrukcja Windows: [INSTALLATION.md](INSTALLATION.md).

## Szybki start — Linux i CARLA 0.9.16

Na Debianie/Ubuntu z aktywnym pulpitem, po zainstalowaniu
[uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
sudo apt update
sudo apt install git python3-venv python3-tk
git clone https://github.com/TMLMarJ/CARLA_SIMULATOR_MANAGER.git
cd CARLA_SIMULATOR_MANAGER
uv sync --locked --python 3.12 --extra carla-0-9-16 --no-dev
source .venv/bin/activate
export CARLA_PYTHONAPI_PATH="$HOME/CARLA_0.9.16/PythonAPI/carla"
python -c "import carla, importlib.metadata as m; print(m.version('carla')); print(carla.__file__)"
python -c "from core.carla_pythonapi import load_basic_agent; a,e=load_basic_agent(); print('BasicAgent OK' if a else e)"
```

CARLA 0.9.15 wymaga profilu `carla-0-9-15` i zalecanego Pythona 3.10.
Natywny serwer uruchom w osobnym terminalu przez
`./CarlaUE4.sh -quality-level=Low`; po załadowaniu świata uruchom w aktywnym
venv `python app.py`. Dla serwera na innym komputerze pomiń lokalny start i
wpisz jego adres w GUI. Szczegóły, wymagania x86-64/glibc, X11/Wayland i WSL2
opisuje [Linux Setup](docs/LINUX_SETUP.md).

## CARLA Python API i Agents

Te elementy pełnią różne role:

- `carla` to binarny moduł instalowany przez wybrany profil uv albo jawne
  polecenie pip z dokładnym numerem wersji;
- `PythonAPI/carla/agents` to kod Python z tej samej wersji CARLA, zawierający
  m.in. `BasicAgent` i `GlobalRoutePlanner`;
- `CARLA_PYTHONAPI_PATH` jest odczytywana przez Manager i powinna wskazywać
  katalog `PythonAPI/carla`, który bezpośrednio zawiera `agents/`;
- `PYTHONPATH` modyfikuje importy całego interpretera. Nie jest wymagany przez
  Managera i może przypadkowo załadować Agents z innej wersji.

Przykłady:

```powershell
$env:CARLA_PYTHONAPI_PATH = 'C:\CARLA_0.9.16\PythonAPI\carla'
```

```bash
export CARLA_PYTHONAPI_PATH="$HOME/CARLA_0.9.16/PythonAPI/carla"
```

Można zamiast tego ustawić `CARLA_ROOT` na katalog główny dystrybucji. Jeśli
korzystasz z osobno pobranych źródeł Agents, muszą mieć dokładnie ten sam tag
CARLA co moduł `carla` i serwer.

## Pierwsze połączenie i przykład użycia

1. Poczekaj, aż serwer załaduje świat.
2. W GUI pozostaw `127.0.0.1` i port `2000` dla serwera lokalnego.
3. Kliknij **Connect** i poczekaj na **Connected** oraz dane Dashboardu.
4. Zmień pogodę albo wykonaj Spawn jednego pojazdu.
5. Dodaj sensor lub otwórz Movement Control dla aktora Managed.
6. Opcjonalnie zapisz stan przez **Save Configuration**.

Dla serwera LAN podaj jego IP/DNS. Firewall musi przepuszczać RPC 2000 i
streaming 2001 (lub `N` i `N+1` przy niestandardowym porcie). Traffic Manager
używa domyślnie zarezerwowanego portu 8050.

## Poprawne zamykanie

Kliknij **Disconnect**, poczekaj na stan rozłączony, a następnie zamknij GUI.
Zamknięcie klienta nie wyłącza serwera CARLA — serwer zakończ osobno w jego
terminalu/oknie. Aktywną konfigurację zapisz przed zamknięciem, jeśli ma być
ponownie użyta.

## Najczęstsze problemy

- **`No module named carla`** — aktywuj właściwe venv i zainstaluj dokładnie
  wersję serwera.
- **Brak BasicAgent/GlobalRoutePlanner** — sprawdź `CARLA_PYTHONAPI_PATH` i plik
  `agents/navigation/basic_agent.py`; nie wskazuj katalogu `dist`.
- **Timeout** — sprawdź serwer, Host, RPC 2000 i streaming 2001. Windows:
  `Test-NetConnection <host> -Port 2000`; Linux: `nc -vz <host> 2000`.
- **PowerShell blokuje aktywację** — użyj dla bieżącej sesji
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.
- **Brak Tkinter/display/Pygame** — uruchom w aktywnej sesji graficznej i
  sprawdź wymagania w instrukcji właściwego systemu.
- **Pusty Sensor Preview** — sprawdź istnienie sensora, rendering serwera i port
  strumieniowania.
- **Problem po zmianie mapy** — poczekaj na nowy episode i użyj **Refresh World**;
  nie uruchamiaj kilku klientów sterujących tymi samymi tickami/TM.

## Dokumentacja

- [Instalacja i konfiguracja Windows](INSTALLATION.md)
- [Instalacja Linux i WSL2](docs/LINUX_SETUP.md)
- [Indeks dokumentacji](docs/README.md)
- [Architektura](docs/ARCHITECTURE.md)
- [Komunikacja z CARLA](docs/CARLA_COMMUNICATION.md)
- [Funkcjonalności GUI](docs/GUI_FUNCTIONALITIES.md)
- [Configuration Management](docs/SCENARIO_MANAGEMENT.md)
- [Movement Control](docs/MOVEMENT_CONTROL.md)
- [Testowanie](docs/TESTING.md)
- [Audyt kompatybilności Linux](docs/LINUX_COMPATIBILITY_AUDIT.md)
- [Materiał techniczny do pracy inżynierskiej](docs/THESIS_TECHNICAL_REFERENCE.md)

## Ograniczenia

- CARLA 1.0 jest poza zakresem;
- natywny Linux i Linux + CARLA 0.9.15 wymagają dalszej weryfikacji;
- `pyproject.toml` jest źródłem bezpośrednich zależności, `uv.lock` zapewnia
  powtarzalne rozwiązanie, a `requirements.txt` pozostaje krótkim fallbackiem
  dla pip;
- Adaptive Speed i BasicAgent zachowują ograniczenia opisane w dokumentacji
  Movement Control.

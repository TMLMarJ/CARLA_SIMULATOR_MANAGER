# Linux Setup

> **Status:** wstępnie sprawdzono Ubuntu 26.04.1 LTS w WSL2/WSLg, Python
> 3.12.16 i CARLA Python API 0.9.16. Nie był to pełny E2E ani test natywnego
> Linuxa. Przed użyciem przeczytaj
> [audyt kompatybilności](LINUX_COMPATIBILITY_AUDIT.md).

## Zakres i wymagania

Docelowe środowisko to Linux x86-64 z aktywną sesją graficzną X11, Wayland albo
WSLg, zapisywalnym klonem projektu oraz zgodnym serwerem CARLA. Tryb headless,
SSH bez display i ARM64 nie są objęte instrukcją.

| Konfiguracja | Status |
|---|---|
| Windows 11 GUI + Windows CARLA 0.9.16 | podstawowe środowisko rozwojowe |
| Ubuntu 26.04.1 WSL2 GUI + Windows CARLA 0.9.16 | wstępnie sprawdzone, niepełne E2E |
| Natywny Linux GUI/server 0.9.16 | wymaga smoke testu |
| Linux/WSL2 + CARLA 0.9.15 | nieweryfikowane |

| CARLA | Python zalecany | Ograniczenie oficjalnego wheel Linux |
|---|---|---|
| 0.9.15 | 3.10 | CPython 3.7–3.10, x86-64, glibc 2.27+ |
| 0.9.16 | 3.12 | CPython 3.10–3.12, x86-64, glibc 2.31+ |

Źródła wheel: [0.9.15](https://pypi.org/project/carla/0.9.15/) i
[0.9.16](https://pypi.org/project/carla/0.9.16/). Dobierz również pakiet serwera
z [oficjalnych wydań CARLA](https://github.com/carla-simulator/carla/releases).

Na Debianie/Ubuntu potrzebne są przynajmniej Git, venv i Tkinter. Nazwy pakietów
na innych dystrybucjach są inne:

```bash
sudo apt update
sudo apt install git python3-venv python3-tk
```

Sprawdź `python3 --version`. Jeżeli domyślna wersja nie pasuje do tabeli, użyj
zainstalowanego interpretera o konkretnej nazwie, np. `python3.10` lub
`python3.12`. Instalacja właściwego interpretera zależy od dystrybucji i nie
powinna polegać na przypadkowym zamienianiu systemowego `python3`.

## Pobranie i środowisko

Zalecana ścieżka korzysta z `uv.lock`. Zainstaluj
[uv](https://docs.astral.sh/uv/getting-started/installation/), a następnie dla
CARLA 0.9.16 wykonaj:

```bash
git clone https://github.com/TMLMarJ/CARLA_SIMULATOR_MANAGER.git
cd CARLA_SIMULATOR_MANAGER
uv sync --locked --python 3.12 --extra carla-0-9-16 --no-dev
source .venv/bin/activate
python --version
```

Dla CARLA 0.9.15 i Pythona 3.10 utwórz osobne środowisko:

```bash
uv sync --locked --python 3.10 --extra carla-0-9-15 --no-dev
source .venv/bin/activate
```

Nie używaj obu extras ani `--all-extras`: profile zawierają sprzeczne, dokładne
wersje modułu `carla` i są oznaczone jako konfliktowe.

Alternatywa bez uv pozostaje dostępna przez standardowe venv i pip. Dla 0.9.16:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install "carla==0.9.16"
```

Dla 0.9.15 zamień interpreter na `python3.10` i ostatnią wersję na
`carla==0.9.15`. `requirements.txt` zawiera wyłącznie bezpośredni runtime;
narzędzia Jupyter i IDE nie są instalowane.

## Konfiguracja CARLA Python API

Użyj katalogu Agents z tej samej wersji co wheel i serwer. Może pochodzić z
rozpakowanego pakietu CARLA albo checkoutu odpowiadającego tagu źródeł. Przykład:

```bash
export CARLA_PYTHONAPI_PATH="$HOME/CARLA_0.9.16/PythonAPI/carla"
```

Można zamiast tego ustawić `CARLA_ROOT="$HOME/CARLA_0.9.16"`. Zmienną trzeba
ustawiać w każdym nowym terminalu albo dodać świadomie do konfiguracji używanej
powłoki.

```bash
python -c "import carla, importlib.metadata as m; print(m.version('carla')); print(carla.__file__)"
python -c "from core.carla_pythonapi import load_basic_agent; a,e=load_basic_agent(); print('BasicAgent OK' if a else e)"
```

Oczekiwany jest właściwy numer wersji, ścieżka w `.venv` i `BasicAgent OK`.
Nie ustawiaj `PYTHONPATH`, jeśli nie jest potrzebny innym narzędziom: Manager sam
dodaje `CARLA_PYTHONAPI_PATH` podczas importu Agents.

## Dialogi, scroll i cache

Wszystkie dialogi aplikacji używają `tkinter.messagebox`; Zenity i Xmessage nie
są wymagane. Kółko myszy obsługuje zarówno `<MouseWheel>`, jak i eventy X11
`<Button-4>/<Button-5>`. Na nietypowym compositorze nadal dostępny jest pasek
przewijania.

Wygenerowany cache map jest zapisywany w:

```text
$XDG_CACHE_HOME/carla-simulator-manager/maps/generated
```

Jeśli `XDG_CACHE_HOME` nie jest ustawiony lub nie jest ścieżką absolutną,
aplikacja używa `~/.cache/carla-simulator-manager/maps/generated`. Stare cache z
repozytorium pozostają źródłem tylko do odczytu. Configuration JSON/YAML są
zapisywane wyłącznie w lokalizacji wybranej przez użytkownika. W nietypowym
środowisku bez możliwego do ustalenia katalogu domowego cache awaryjnie trafia
do katalogu zwracanego przez `tempfile.gettempdir()`.

## Uruchomienie lokalne

W pierwszym terminalu uruchom serwer:

```bash
cd "$HOME/CARLA_0.9.16"
./CarlaUE4.sh -quality-level=Low
```

Oficjalny quickstart opisuje wymagania sprzętowe i argumenty serwera:
[CARLA quickstart](https://carla.readthedocs.io/en/0.9.15/start_quickstart/).
Serwer wymaga poprawnego sterownika GPU i środowiska graficznego właściwego dla
dystrybucji CARLA.

W drugim terminalu:

```bash
cd "$HOME/CARLA_SIMULATOR_MANAGER"
source .venv/bin/activate
export CARLA_PYTHONAPI_PATH="$HOME/CARLA_0.9.16/PythonAPI/carla"
python app.py
```

W GUI pozostaw `127.0.0.1:2000`, kliknij **Connect** i oczekuj statusu
**Connected**. Po pracy kliknij **Disconnect**, zamknij GUI, a serwer zatrzymaj
osobno.

## Serwer na innym komputerze

Linuxowy klient GUI może połączyć się z serwerem Windows lub Linux,
jeżeli wersje CARLA są identyczne i sieć przepuszcza RPC 2000 oraz streaming
2001 (albo port `N` i `N+1` dla niestandardowego RPC). Traffic Manager może
wymagać również portu 8050. Połączenie WSL2–Windows zostało wstępnie
sprawdzone dla CARLA 0.9.16; pozostałe warianty wymagają osobnego smoke testu.

```bash
nc -vz 192.168.1.50 2000
nc -vz 192.168.1.50 2001
```

`nc` jest tylko narzędziem diagnostycznym i może wymagać osobnego pakietu.

## WSL2: GUI w Ubuntu, serwer CARLA w Windows

WSL2 uruchamia Linux w maszynie wirtualnej. W Windows 11 WSLg może wyświetlać
okna Tkinter i Pygame bez ręcznego serwera X. Najpierw sprawdź warstwę GUI:

```bash
echo "$DISPLAY"
echo "$WAYLAND_DISPLAY"
python -c "import tkinter as tk; r=tk.Tk(); r.after(500, r.destroy); r.mainloop()"
python -c "import pygame; pygame.init(); print(pygame.display.get_driver()); pygame.quit()"
```

Uruchom CARLA w Windows, a Managera wewnątrz WSL2. `localhost` może działać
w zależności od wersji WSL i trybu sieci, ale nie należy tego zakładać. Jeżeli
`127.0.0.1:2000` nie odpowiada, odczytaj adres hosta Windows w domyślnym trybie
NAT:

```bash
WINDOWS_HOST_IP="$(ip route show default | awk '{print $3}')"
printf '%s\n' "$WINDOWS_HOST_IP"
nc -vz "$WINDOWS_HOST_IP" 2000
nc -vz "$WINDOWS_HOST_IP" 2001
```

Wpisz ten adres w polu **Host**. Zapora Windows musi zezwalać na połączenia do
CARLA w używanym profilu sieciowym. Nie wyłączaj zapory globalnie; dodaj regułę
dla aplikacji lub wymaganych portów.

W konfiguracji Ubuntu 26.04.1 LTS/WSL2, Python 3.12.16 i API 0.9.16 wstępnie
sprawdzono uruchomienie GUI, Sensor Preview, Movement Control, Configuration
Save/Load, Disconnect/Reconnect oraz synchronizację dwóch klientów, także po
zmianie mapy. Była to weryfikacja funkcjonalna, nie pełny E2E.

## Typowe problemy

- **`No module named carla`** — aktywuj `.venv` i zainstaluj dokładnie właściwy
  wheel; sprawdź architekturę, Python i glibc (`ldd --version`).
- **Brak `agents`/BasicAgent** — ustaw `CARLA_PYTHONAPI_PATH` na katalog
  `PythonAPI/carla` z tej samej dystrybucji serwera.
- **`No module named tkinter`** — zainstaluj pakiet Tk dla konkretnej wersji
  Pythona; nazwa zależy od dystrybucji.
- **`pygame.error: No available video device`** — uruchom w aktywnej sesji
  pulpitu i sprawdź `echo "$XDG_SESSION_TYPE"`, `echo "$DISPLAY"` oraz
  `echo "$WAYLAND_DISPLAY"`.
- **Nie można zapisać cache mapy** — sprawdź uprawnienia do `XDG_CACHE_HOME` lub
  `~/.cache`; drzewo projektu nie musi być zapisywalne.
- **Puste Sensor Preview** — sprawdź rendering serwera, istnienie sensora,
  streaming i aktywną sesję graficzną.
- **Proces Pygame pozostał po zamknięciu** — zakończ GUI, sprawdź procesy Python
  i zapisz wynik do raportu smoke; nie stosuj automatycznego killowania innych
  sesji użytkownika.

## Ograniczenia

Wstępny test WSL2 nie potwierdza działania na natywnym Linuxie, innych
compositorach, GPU ani z CARLA 0.9.15. Należy wykonać Minimal Linux Smoke Test
Plan z audytu na natywnym Linuxie oraz osobną kontrolę CARLA 0.9.15.

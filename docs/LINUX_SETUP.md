# Linux Setup

> **Status:** kod zawiera implementację przenośnych dialogów, scrolla, cache i
> multiprocessing. Procedura nie została jeszcze wykonana na rzeczywistym
> Linuxie z CARLA, dlatego wymaga smoke testu przed deklaracją pełnej zgodności
> konkretnej dystrybucji. Przed użyciem przeczytaj
> [audyt kompatybilności](implementation/LINUX_COMPATIBILITY_AUDIT.md).

## Zakres i wymagania

Docelowe środowisko to Linux x86-64 z aktywną sesją graficzną X11 albo
Wayland, zapisywalnym klonem projektu oraz zgodnym serwerem CARLA. Tryb headless,
SSH bez display i ARM64 nie są objęte instrukcją.

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

```bash
git clone https://github.com/TMLMarJ/CARLA_SIMULATOR_MANAGER.git
cd CARLA_SIMULATOR_MANAGER
python3.12 -m venv .venv
source .venv/bin/activate
python --version
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install "carla==0.9.16"
```

Dla CARLA 0.9.15 użyj Pythona 3.10 i `carla==0.9.15`.
`requirements.txt` nie instaluje CARLA Python API; dlatego ostatnie polecenie
jest obowiązkowe.

## Konfiguracja CARLA Python API

Rozpakuj tę samą wersję serwera, na przykład do `$HOME/CARLA_0.9.16`, i ustaw:

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
wymagać również portu 8050. Ta konfiguracja nie została przetestowana.

```bash
nc -vz 192.168.1.50 2000
nc -vz 192.168.1.50 2001
```

`nc` jest tylko narzędziem diagnostycznym i może wymagać osobnego pakietu.

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

Testy mockowane nie potwierdzają działania Wayland, X11, różnych compositorów,
GPU ani prawdziwego serwera. Należy wykonać Minimal Linux Smoke Test Plan z
audytu oraz kontrolę manualną na CARLA 0.9.15 i 0.9.16.

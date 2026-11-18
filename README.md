# CARLA Simulator Manager

CARLA Simulator Manager to desktopowa aplikacja w Pythonie (Tkinter), która
udostępnia graficzny interfejs do zarządzania działającą symulacją CARLA. GUI
łączy ustawienia świata, aktorów, sensory, sterowanie ruchem, podglądy oraz zapis
i odczyt **Simulation Configuration**.

CARLA działa w architekturze klient–serwer:

- **serwer CARLA** uruchamia świat i symulację 3D (`CarlaUE4.exe` na Windows lub
  `CarlaUE4.sh` na Linuxie);
- **CARLA Simulator Manager** jest klientem GUI, który łączy się z serwerem
  przez sieć i wysyła polecenia przy użyciu CARLA Python API.

Projekt nie pobiera ani nie uruchamia serwera CARLA automatycznie. Przed użyciem
GUI trzeba osobno uruchomić kompatybilny serwer.

## Najważniejsze funkcje

- połączenie, rozłączenie i ponowne połączenie z lokalnym lub zdalnym serwerem;
- zmiana mapy, pogody i `WorldSettings`;
- Spawn, edycja, Remove i przejmowanie aktorów Managed/External;
- sensory, ich parametry i podglądy;
- Manual Control, Free Roam i Destination dla pojazdów;
- tryby sync/async, Run/Pause/Step oraz dashboard symulacji;
- Spectator Preview i Movement Preview z HUD;
- Save, Preflight, Load, Replace i Clear konfiguracji JSON/YAML.

Configuration Management zapisuje stan obsługiwany przez aplikację, a nie
pełny, deterministyczny scenariusz testowy z gwarantowanym przebiegiem ruchu.
Wewnętrzne nazwy `ScenarioManager`, `scenarios/` i klucze starszych plików
pozostają zachowane dla kompatybilności.

## Obsługiwane wersje

Projekt obsługuje Windows i Linux oraz CARLA 0.9.15 i 0.9.16. Wersją
referencyjną jest 0.9.16. Automatyczne testy przenośności są wykonywane na
Windows z mockami platformowymi; rzeczywisty smoke test Linux, X11/Wayland i
CARLA nadal jest wymagany przed deklaracją pełnej zgodności środowiskowej.
Szczegóły zawiera [audyt kompatybilności](docs/implementation/LINUX_COMPATIBILITY_AUDIT.md).

| CARLA | Oficjalne wheel CPython | Zalecenie dla projektu |
|---|---|---|
| 0.9.15 | 3.7–3.10 | Python 3.10 |
| 0.9.16 | 3.10–3.12 | Python 3.12 (środowisko referencyjne) |

Tabela opisuje pliki opublikowane dla Windows x86-64 i Linux x86-64, nie wynik
testów projektu. Używaj tej samej wersji serwera, modułu `carla` i katalogu
`PythonAPI/carla/agents`. Źródła: [CARLA 0.9.15](https://github.com/carla-simulator/carla/releases/tag/0.9.15),
[CARLA 0.9.16](https://github.com/carla-simulator/carla/releases/tag/0.9.16),
[wheel 0.9.15](https://pypi.org/project/carla/0.9.15/) i
[wheel 0.9.16](https://pypi.org/project/carla/0.9.16/).

## Wymagania

- Windows 11 (środowisko podstawowe) i aktywna sesja graficzna;
- Python zgodny z wybraną wersją CARLA;
- CARLA 0.9.15 lub 0.9.16 wraz z katalogiem `PythonAPI`;
- Tkinter (standardowy instalator Pythona dla Windows zwykle go zawiera);
- zależności z [requirements.txt](requirements.txt).

Kod aplikacji korzysta m.in. z NumPy, Pillow, Pygame, Matplotlib z backendem Tk,
PyYAML i Pygments. Dialogi korzystają wyłącznie ze standardowego
`tkinter.messagebox`; Zenity i Xmessage nie są wymagane. `requirements.txt` jest
nieprzypiętym eksportem szerszego środowiska i zawiera też narzędzia Jupyter.
Nie zawiera aktywnej, przenośnej instalacji `carla`: zakomentowany wpis wskazuje
lokalny wheel Windows i nie jest wykonywany przez `pip`.

## Pierwsze uruchomienie na Windows — krok po kroku

Przykłady używają CARLA 0.9.16 i Pythona 3.12. Dla CARLA 0.9.15 zamień numer
wersji i użyj Pythona 3.10.

### 1. Pobierz serwer CARLA

Pobierz gotowy pakiet z oficjalnych
[wydań CARLA](https://github.com/carla-simulator/carla/releases) i rozpakuj go,
na przykład do `C:\CARLA_0.9.16`. Powinny istnieć co najmniej:

```text
C:\CARLA_0.9.16\CarlaUE4.exe
C:\CARLA_0.9.16\PythonAPI\carla\agents
```

Nie mieszaj plików z dwóch wersji CARLA.

### 2. Pobierz projekt

W PowerShell wykonaj:

```powershell
git clone https://github.com/TMLMarJ/CARLA_SIMULATOR_MANAGER.git
cd CARLA_SIMULATOR_MANAGER
```

Alternatywnie wybierz w repozytorium **Code → Download ZIP**, rozpakuj archiwum,
otwórz jego katalog w Eksploratorze, kliknij pasek adresu, wpisz `powershell` i
naciśnij Enter. Następne polecenia wykonuj w katalogu z `app.py`.

### 3. Utwórz środowisko wirtualne

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
```

Oczekiwany rezultat to prefiks `(.venv)` i Python 3.12.x. Dla CARLA 0.9.15
użyj `py -3.10 -m venv .venv`. Jeśli `py` nie istnieje, sprawdź
`python --version` i użyj `python -m venv .venv`, o ile wersja jest właściwa.

### 4. Zainstaluj zależności i CARLA Python API

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install "carla==0.9.16"
```

Dla CARLA 0.9.15 ostatnie polecenie to:

```powershell
python -m pip install "carla==0.9.15"
```

CARLA Python API jest modułem, przez który aplikacja komunikuje się z serwerem.
Wersja klienta musi odpowiadać serwerowi; samo `pip install -r requirements.txt`
tego nie zapewnia.

### 5. Wskaż oficjalny katalog `agents`

Destination korzysta z `BasicAgent` i `GlobalRoutePlanner`, które znajdują się
w pakiecie serwera CARLA:

```powershell
$env:CARLA_PYTHONAPI_PATH = 'C:\CARLA_0.9.16\PythonAPI\carla'
```

Zmienna obowiązuje w bieżącym PowerShell. Zamiast niej można ustawić
`CARLA_ROOT` na `C:\CARLA_0.9.16`. Sprawdź środowisko:

```powershell
python -c "import carla, importlib.metadata as m; print(m.version('carla')); print(carla.__file__)"
python -c "from core.carla_pythonapi import load_basic_agent; a,e=load_basic_agent(); print('BasicAgent OK' if a else e)"
```

Pierwsze polecenie powinno wypisać `0.9.16` i ścieżkę z `.venv`, a drugie
`BasicAgent OK`.

### 6. Uruchom serwer CARLA

Otwórz **drugie** okno PowerShell:

```powershell
cd C:\CARLA_0.9.16
.\CarlaUE4.exe -quality-level=Low
```

Poczekaj na okno świata CARLA. Domyślny port RPC to `2000`, a strumieniowania
danych `2001`. Opcję jakości można pominąć na mocniejszym komputerze.

### 7. Uruchom GUI

W pierwszym PowerShell, nadal w katalogu projektu i z aktywnym `(.venv)`:

```powershell
python app.py
```

`app.py` jest punktem wejścia. Oczekiwanym rezultatem jest główne okno Managera.

### 8. Połącz się i sprawdź działanie

1. Pozostaw Host/IP `127.0.0.1` i Port `2000`.
2. Kliknij **Connect**.
3. Poczekaj na status **Connected** i dane Dashboardu.
4. Sprawdź zmianę pogody, listę aktorów lub Spawn pojedynczego aktora.
5. Configuration Management zapisuje i odtwarza obsługiwany stan w JSON/YAML.

### 9. Zakończ pracę

Kliknij **Disconnect**, potem zamknij GUI. To zatrzymuje zasoby klienta, ale
**nie wyłącza serwera CARLA**. Serwer zakończ osobno w jego oknie (Ctrl+C lub
zamknięcie okna). Przedtem zapisz potrzebną konfigurację.

## Połączenie z serwerem na innym komputerze

Podaj adres IPv4 lub DNS komputera z serwerem zamiast `127.0.0.1`. Firewall
serwera musi przepuszczać port RPC (domyślnie TCP 2000) i strumieniowania
(domyślnie 2001). Przy `-carla-rpc-port=N` drugi port to zwykle `N+1`. Traffic
Manager może wymagać dostępności skonfigurowanego portu aplikacji (domyślnie
8050), zależnie od topologii klientów.

```powershell
Test-NetConnection 192.168.1.50 -Port 2000
Test-NetConnection 192.168.1.50 -Port 2001
```

Udany test portu nie gwarantuje zgodności API. Na kliencie nadal musi być ten
sam numer CARLA co na serwerze. Aplikacja nie konfiguruje firewalla ani routera.

## Najczęstsze problemy

### `ModuleNotFoundError: No module named 'carla'`

Aktywuj właściwe `.venv`, zainstaluj `carla==...` i ponów kontrolę z kroku 5.

### Brak `BasicAgent` lub `GlobalRoutePlanner`

Ustaw `CARLA_PYTHONAPI_PATH` na katalog zawierający `agents/` z tej samej
dystrybucji CARLA. Nie wskazuj katalogu `dist` ani wheel. Uruchom GUI ponownie.

### Brak połączenia lub timeout

Sprawdź działanie serwera, Host, Port, zakończenie ładowania mapy i porty
2000/2001. Przy połączeniu zdalnym użyj `Test-NetConnection`. Po zmianie mapy
poczekaj na nowy episode i w razie potrzeby użyj **Refresh World**.

### Błędna wersja CARLA Python API

Numer z `importlib.metadata.version('carla')` musi zgadzać się z serwerem:

```powershell
python -m pip uninstall carla
python -m pip install "carla==0.9.16"
```

### Brak biblioteki lub błędne środowisko

`where.exe python` powinno najpierw wskazać `.venv\Scripts\python.exe`. Ponownie
wykonaj `python -m pip install -r requirements.txt` w aktywnym środowisku.

### PowerShell blokuje aktywację `.venv`

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Zmiana dotyczy tylko bieżącego okna PowerShell.

### Błąd Pygame/display lub podgląd nie otwiera się

Uruchom GUI w interaktywnej sesji pulpitu, zamknij stare podglądy i sprawdź
`python -c "import pygame; print(pygame.version.ver)"`. Pulpit zdalny, usługa lub
brak aktywnego wyświetlacza może uniemożliwić utworzenie okna.

### Sensor Preview nie pokazuje obrazu

Sprawdź istnienie i attachment sensora oraz czy serwer renderuje kamery. Zamknij
podgląd, użyj **Refresh World** i otwórz go ponownie. Dla serwera zdalnego
sprawdź port strumieniowania.

### Zmiana mapy nie kończy się poprawnie

Zatrzymaj aktywne tryby ruchu i podglądy. Nie używaj równocześnie drugiego
klienta sterującego tickami lub tym samym Traffic Managerem. Wybrana mapa musi
należeć do pakietu serwera.

## Dokumentacja

- [Szczegółowa instalacja](INSTALLATION.md)
- [Indeks dokumentacji](docs/README.md)
- [Funkcjonalności GUI](docs/GUI_FUNCTIONALITIES.md)
- [Configuration Management](docs/SCENARIO_MANAGEMENT.md)
- [Movement Control](docs/MOVEMENT_CONTROL.md)
- [Komunikacja z CARLA](docs/CARLA_COMMUNICATION.md)
- [Audyt kompatybilności Linux](docs/implementation/LINUX_COMPATIBILITY_AUDIT.md)
- [Instalacja Linux](docs/LINUX_SETUP.md)
- [Raport implementacji Windows/Linux](docs/implementation/LINUX_COMPATIBILITY_IMPLEMENTATION.md)
- [Testowanie](docs/TESTING.md)

## Ważne ograniczenia

- implementacja jest przenośna na Windows/Linux, ale rzeczywisty Linux wymaga
  jeszcze smoke testów integracyjnych i E2E;
- `requirements.txt` nie przypina wersji i nie stanowi lockfile.
- aplikacja zachowuje politykę Managed/External/Internal oraz Exclusive/Shared
  Traffic Manager opisaną w dokumentacji technicznej;
- build CARLA 0.9.16 używany w projekcie nie udostępnia RSS;
- ograniczenia Destination opisuje [Movement Control](docs/MOVEMENT_CONTROL.md).

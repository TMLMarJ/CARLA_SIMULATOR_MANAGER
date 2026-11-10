# Instalacja CARLA Simulator Manager

## Wymagania środowiskowe

- Windows z Pythonem zgodnym z CARLA 0.9.16;
- CARLA 0.9.16;
- Tkinter dostępny w instalacji Pythona;
- importowalny moduł `carla` zgodny z serwerem oraz oficjalny katalog
  `PythonAPI/carla/agents` z tej samej instalacji.

Projekt nie instaluje ani nie uruchamia serwera CARLA automatycznie.

## Środowisko Python

Z katalogu projektu utwórz i aktywuj środowisko wirtualne:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.txt` deklaruje wersjonowane zależności PyYAML i Pillow. Kod używa również pakietów dostarczanych przez środowisko CARLA lub instalowanych osobno: `carla`, NumPy, Matplotlib, Pygame i Pygments. Projekt nie określa ich wersji, dlatego należy dobrać je do używanej dystrybucji CARLA i interpretera zamiast instalować przypadkowe wersje. Nie należy instalować przypadkowego pakietu `agents` z innej dystrybucji.

## CARLA Python API

Moduł `carla` musi być importowalny w wybranym interpreterze. Resolver
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
[CARLA_COMMUNICATION.md](docs/architecture/CARLA_COMMUNICATION.md).

## Testy bez serwera

```powershell
python -m unittest discover -s tests -t .
```

Testy automatyczne używają fake'ów i mocków. Nie potwierdzają zgodności sieciowej, renderowania GPU ani zachowania prawdziwych okien Tkinter/Pygame.

## Typowe problemy

- **`ModuleNotFoundError: carla`** — moduł CARLA nie jest dostępny w aktywnym interpreterze.
- **Brak GlobalRoutePlanner lub BasicAgent** — ustaw poprawnie `CARLA_PYTHONAPI_PATH` lub `CARLA_ROOT`, sprawdź zgodność z CARLA 0.9.16 i uruchom aplikację ponownie.
- **Konflikt modułu `agents`** — usuń z `sys.path` inną paczkę o tej nazwie; resolver odrzuca planer załadowany spoza skonfigurowanego PythonAPI.
- **Brak Tkinter/Pygame** — użyj interpretera z Tkinter i doinstaluj zależności podglądów zgodne z CARLA.
- **Timeout połączenia** — sprawdź, czy serwer działa, port jest dostępny i GUI wskazuje właściwy host.
- **Błąd portu Traffic Managera** — zarezerwuj osobny wolny port dla każdej instancji aplikacji korzystającej z ekskluzywnego TM.
- **Brak RSS** — aktualny build CARLA Python client nie udostępnia RSS; aplikacja informuje, że taki podgląd jest niedostępny.
- **Brak starego cache map** — aplikacja czyta także dotychczasowy katalog `images/maps/generated/`, a nowe obrazy zapisuje w `resources/maps/generated/`.

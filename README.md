# CARLA Simulator Manager

Desktopowa aplikacja Tkinter do zarządzania CARLA 0.9.16. Łączy obsługę
świata, aktorów, sensorów, symulacji, scenariuszy i ruchu w jednym GUI, przy
czym operacje CARLA są wykonywane przez dedykowany worker aplikacji.

## Najważniejsze funkcje

- połączenie, rozłączenie i ponowne połączenie z lokalnym lub zdalnym serwerem CARLA;
- zmiana mapy, pogody i ustawień symulacji;
- tworzenie, edycja, usuwanie i przejmowanie aktorów GUI-managed/external;
- obsługa sensorów i ich podglądów;
- sterowanie pojazdami i pieszymi;
- tryby synchroniczny/asynchroniczny, pause/resume i pojedynczy tick;
- zapis, walidacja, odczyt i czyszczenie scenariuszy JSON/YAML;
- podgląd Spectatora oraz generowanie map OpenDRIVE.

## Vehicle Destination

Domyślnym executorem jest oficjalny `BasicAgent` z PythonAPI CARLA 0.9.16.
`GlobalRoutePlanner` wyznacza plan `(Waypoint, RoadOption)`, a agent wykonuje
go cyklicznie w workerze `CarlaController`; Traffic Manager nie steruje w tym
czasie tym samym pojazdem.

W oknie Movement Control można przed rozpoczęciem Destination ustawić
**Maximum speed** (10–90 km/h, domyślnie 40 km/h) oraz włączyć eksperymentalny
profil **Adaptive speed**. Ustawienia należą do konkretnego przejazdu i są
zablokowane w czasie jazdy, również po zamknięciu i ponownym otwarciu okna.
Zamknięcie samego okna nie anuluje aktywnego Destination.

Executory `path` i `route` pozostają dostępne jako warianty diagnostyczne i
legacy przez opcjonalną zmienną `CARLA_MANAGER_DESTINATION_EXECUTOR`.

## Wymagania

- Python zgodny z używaną wersją CARLA Python API;
- CARLA 0.9.16 wraz z modułem Python `carla`;
- Tkinter;
- zależności z [requirements.txt](requirements.txt);
- zależności runtime używane przez podglądy i wykresy: NumPy, Matplotlib, Pygame oraz Pygments.

Szczegółowa konfiguracja środowiska znajduje się w [INSTALLATION.md](INSTALLATION.md).

## Uruchomienie

Uruchom serwer CARLA, a następnie z katalogu projektu:

```powershell
$env:CARLA_PYTHONAPI_PATH = 'C:\CARLA_0.9.16\PythonAPI\carla'
python app.py
```

`CARLA_PYTHONAPI_PATH` jest opcjonalny, jeżeli poprawny pakiet `agents` jest
już importowalny. Do wyboru BasicAgent nie jest wymagana żadna zmienna
środowiskowa związana z Destination. Endpoint serwera podaje się w Connection
Managerze; domyślnie jest to `127.0.0.1:2000`. `app.py` jest jedynym punktem
wejścia aplikacji.

## Struktura

| Katalog | Odpowiedzialność |
|---|---|
| `core/` | kontroler, kolejki, kontrakty i współdzielony stan |
| `models/` | modele aktorów i scenariuszy |
| `services/` | operacje CARLA dla świata, aktorów i sensorów |
| `gui/` | aplikacja, główny widok, zakładki, okna i widgety |
| `movement/` | sesja i runtime sterowania ruchem |
| `simulation/` | sterowanie tickami i dashboard |
| `scenarios/` | zapis, odczyt, synchronizacja i runtime scenariuszy |
| `preview/` | procesy i bufory podglądów oraz generator map |
| `resources/maps/` | mapy dostarczane z projektem i generowany cache |
| `tests/` | testy automatyczne według funkcjonalności |
| `docs/` | dokumentacja architektury i testów |

## Dokumentacja

- [Indeks dokumentacji](docs/README.md)
- [Przegląd architektury](docs/architecture/OVERVIEW.md)
- [Moduły i zależności](docs/architecture/MODULES.md)
- [Komunikacja z CARLA](docs/architecture/CARLA_COMMUNICATION.md)
- [Architektura funkcjonalności](docs/architecture/FEATURES.md)
- [Testy jednostkowe](docs/testing/UNIT_TESTS.md)
- [Audyt pokrycia funkcjonalnego](docs/testing/TEST_COVERAGE.md)
- [Manualne testy E2E](docs/testing/MANUAL_E2E_CHECKLIST.md)

## Testy

Testy jednostkowe i komponentowe nie uruchamiają serwera CARLA:

```powershell
python -m unittest discover -s tests -t .
```

Testy live CARLA oraz końcowa checklista GUI pozostają manualne.

## Ograniczenia

- build CARLA 0.9.16 używany przez projekt nie udostępnia obsługi RSS;
- Adaptive Speed jest heurystyką opartą na geometrii 2D planu, a nie modelem
  przyczepności ani gwarancją utrzymania pasa;
- BasicAgent zachowuje własne ograniczenia wykrywania przeszkód, szczególnie
  pieszych i części obiektów statycznych;
- executory TM `path` i `route` są utrzymywane diagnostycznie; `route` nie
  reprezentuje wymaganych zmian pasa, a `path` był mniej niezawodny przy
  wykonywaniu skrętów w dotychczasowych testach live.

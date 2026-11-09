# CARLA Simulator Manager

Desktopowa aplikacja Tkinter do zarządzania symulatorem CARLA. Umożliwia obsługę świata i pogody, aktorów, sensorów, sterowania ruchem, przebiegu symulacji, scenariuszy oraz podglądów.

## Najważniejsze funkcje

- połączenie, rozłączenie i ponowne połączenie z serwerem CARLA;
- zmiana mapy, pogody i ustawień symulacji;
- tworzenie, edycja, usuwanie i przejmowanie aktorów;
- obsługa sensorów i ich podglądów;
- sterowanie pojazdami i pieszymi;
- tryby synchroniczny/asynchroniczny, pause/resume i pojedynczy tick;
- zapis, walidacja, odczyt i czyszczenie scenariuszy JSON/YAML;
- podgląd Spectatora oraz generowanie map OpenDRIVE.

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
python app.py
```

`app.py` jest jedynym punktem wejścia aplikacji. Stary moduł `carla_manager.py` został usunięty po migracji importów.

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
- [Testy jednostkowe](docs/testing/UNIT_TESTS.md)
- [Audyt pokrycia funkcjonalnego](docs/testing/TEST_COVERAGE.md)

## Testy

Testy jednostkowe i komponentowe nie uruchamiają serwera CARLA:

```powershell
python -m unittest discover -s tests -t .
```

Testy live CARLA oraz końcowa checklista GUI pozostają manualne.

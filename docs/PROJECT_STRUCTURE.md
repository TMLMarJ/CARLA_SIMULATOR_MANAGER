# Struktura projektu CARLA Simulator Manager

Dokument przedstawia aktualną organizację repozytorium po refaktoryzacji architektury. Katalogi techniczne `.git/`, `.idea/`, `.venv/` i `__pycache__/` zostały pominięte.

## Główne drzewo projektu

```text
CARLA_SIMULATOR_MANAGER/
├── app.py                       # kanoniczny punkt wejścia
├── core/                        # kontroler, kolejki i stan aplikacji
├── models/                      # modele danych
├── services/                    # operacje domenowe CARLA
├── gui/                         # aplikacja i interfejs Tkinter
│   ├── tabs/                    # główne zakładki
│   ├── windows/                 # okna Toplevel
│   └── widgets/                 # współdzielone komponenty
├── movement/                    # sterowanie ruchem
├── simulation/                  # sterowanie symulacją i dashboard
├── scenarios/                   # zapis, odczyt i synchronizacja scenariuszy
├── preview/                     # podglądy, bufory i mapy
├── resources/                   # zasoby dostarczane i generowane
├── utilities/                   # współdzielone funkcje pomocnicze
├── tests/                       # testy według funkcjonalności
├── docs/                        # dokumentacja projektu
├── README.md
├── INSTALLATION.md
├── requirements.txt
├── CHANGELOG.md
└── WORKFLOW.md
```

## Pliki główne

| Plik | Przeznaczenie |
|---|---|
| `app.py` | Kanoniczne uruchomienie aplikacji; wywołuje `freeze_support()`, tworzy `Manager` i uruchamia `mainloop()`. |
| `README.md` | Ogólny opis projektu i podstawowe uruchomienie. |
| `INSTALLATION.md` | Instalacja, konfiguracja CARLA PythonAPI i typowe problemy. |
| `requirements.txt` | Zależności Python deklarowane przez projekt. |
| `CHANGELOG.md` | Historia zmian projektu. |
| `WORKFLOW.md` | Informacje o przyjętym workflow. |
| `tester_function.ipynb` | Pomocniczy notebook istniejący w repozytorium. |

## `core/` — infrastruktura aplikacji

| Plik | Odpowiedzialność |
|---|---|
| `controller.py` | `CarlaController`, worker, routing poleceń, lifecycle i koordynacja runtime. |
| `command_bus.py` | Kolejki normalne i priorytetowe oraz synchroniczny dispatcher. |
| `contracts.py` | Małe kontrakty współdzielone, m.in. `RuntimeContext`. |
| `state_store.py` | `DataStorage` — współdzielony stan pojedynczej sesji aplikacji. |
| `registries.py` | Rejestry `Actors` i `Sensors`. |
| `carla_pythonapi.py` | Wyszukiwanie dodatkowych modułów CARLA PythonAPI i planera tras. |

## `models/` — modele danych

| Plik | Odpowiedzialność |
|---|---|
| `actors.py` | `ActorInfo` i `SensorInfo` wraz z metadanymi ownership. |
| `scenarios.py` | Modele, enumy i struktury dokumentu scenariusza. |

## `services/` — operacje CARLA

| Plik | Odpowiedzialność |
|---|---|
| `world.py` | Odczyt i zmiana świata/mapy oraz instalacja nowego epizodu. |
| `weather.py` | Zastosowanie ustawień pogody. |
| `actors.py` | Spawn, transformacja, fizyka, usuwanie i ghost actors. |
| `sensors.py` | Attach, edycja, callbacki i cleanup sensorów. |
| `blueprints.py` | Wyszukiwanie blueprintów i obsługa ich atrybutów. |

Usługi nie tworzą własnych workerów ani klientów CARLA. Są wywoływane w wątku `CarlaController`.

## `gui/` — interfejs Tkinter

### Pliki główne

| Plik | Odpowiedzialność |
|---|---|
| `application.py` | `Manager` — root Tkinter, composition root i lifecycle GUI. |
| `main_view.py` | `MainView` — główny Notebook oraz dynamiczne zakładki. |
| `styles.py` | Konfiguracja istniejącego motywu ttk. |

### `gui/tabs/`

- `home.py` — połączenie, Spectator i główny dashboard;
- `world.py` — World & Weather oraz podgląd mapy;
- `vehicles.py` — pojazdy;
- `pedestrians.py` — piesi;
- `static_objects.py` — obiekty statyczne;
- `sensors.py` — sensory;
- `simulation.py` — Scenario Management i Simulation Control.

### `gui/windows/`

- `actor_editor.py` — edycja aktora i fizyki;
- `model_picker.py` — wybór modelu/blueprintu;
- `transform_editor.py` — edycja transformacji;
- `movement_control.py` — Movement Control i wybór celu;
- `sensor_preview.py` — okna podglądu danych sensorów.

### `gui/widgets/`

- `actor_common.py` — małe wspólne operacje tabel aktorów;
- `actor_preview.py` — ghost preview i podgląd kamery;
- `dashboard.py` — prezentacja snapshotów dashboardu;
- `color_picker.py` — wybór koloru.

## `movement/` — sterowanie aktorami

| Plik | Odpowiedzialność |
|---|---|
| `runtime.py` | Wykonawcze handlery pojazdów, pieszych, destination i cleanupu ruchu. |
| `session.py` | Lifecycle pojedynczej sesji Movement Control i procesu podglądu. |
| `destination.py` | Walidacja celu i przeliczenia mapa–obraz. |
| `vehicle.py` | Tryby i struktury stanu sterowania pojazdem. |
| `walker.py` | Tryby i struktury stanu sterowania pieszym. |

## `simulation/` — przebieg symulacji

| Plik | Odpowiedzialność |
|---|---|
| `manager.py` | Maszyna stanów sync/async, pause/resume i single step. |
| `runtime.py` | Tick scheduler oraz wykonawcze handlery symulacji. |
| `dashboard.py` | Budowanie snapshotów dashboardu. |

## `scenarios/` — Scenario Management

| Plik | Odpowiedzialność |
|---|---|
| `manager.py` | Fasada zapisu i odczytu scenariuszy. |
| `runtime.py` | Save, Load, Clear, Synchronization i Take Ownership w workerze. |
| `capture.py` | Zebranie zarządzanych aktorów do dokumentu scenariusza. |
| `serialization.py` | JSON/YAML i zapis atomowy. |
| `validation.py` | Walidacja dokumentu scenariusza. |
| `loader.py` | Preflight przed modyfikacją świata. |
| `restorer.py` | Odtwarzanie aktorów i sensorów. |
| `cleaner.py` | Managed Only i Entire World cleanup. |
| `synchronization.py` | Uzgadnianie aktorów CARLA z rejestrami. |
| `adoption.py` | Take Ownership bez ponownego spawnowania. |
| `classifier.py` | Klasyfikowanie aktorów świata. |
| `converters.py` | Konwersje CARLA ↔ dane serializowalne. |
| `paths.py` | Nazwy, rozszerzenia i normalizacja ścieżek. |

## `preview/` — podglądy i mapy

| Plik | Odpowiedzialność |
|---|---|
| `movement.py` | Osobny proces Pygame dla Movement Preview i protokół IPC. |
| `spectator.py` | Proces Spectator Preview z własnym `carla.Client`. |
| `spectator_controls.py` | Sterowanie kamerą Spectatora. |
| `sensor_buffers.py` | Thread-safe bufory danych sensorów, niezależne od Tkinter. |
| `map.py` | Generowanie oraz cache podglądów map OpenDRIVE. |

## `resources/` i dane generowane

```text
resources/
└── maps/
    ├── bundled/                 # mapy dostarczane z projektem
    └── generated/               # lokalny, ignorowany cache generatora
```

Stary cache `images/maps/generated/` jest nadal odczytywany dla kompatybilności. Dane użytkownika nie są automatycznie przenoszone ani usuwane.

## `tests/` — organizacja testów

```text
tests/
├── core/                        # kontroler, state store, importy i architektura
├── connection/                  # connect/disconnect/reconnect lifecycle
├── world/                       # synchronizacja świata i map preview
├── actors/                      # aktorzy, ownership, ghost i blueprinty
├── sensors/                     # lifecycle sensorów
├── movement/                    # pojazdy, piesi, destination i preview
├── simulation/                  # ticki, tryby i dashboard
└── scenarios/                   # Save/Load/Clear, preflight i synchronizacja
```

Testy używają `unittest` i fake'ów/mocków. Nie uruchamiają automatycznie serwera CARLA.

## `docs/` — dokumentacja

| Katalog lub plik | Zawartość |
|---|---|
| `docs/README.md` | Indeks dokumentacji. |
| `docs/architecture/` | Aktualna architektura, moduły, komunikacja, przepływ danych i historia refaktoryzacji. |
| `docs/testing/` | Organizacja testów, pokrycie funkcjonalne i checklista manualna. |
| `docs/implementation/` | Lokalne raporty implementacyjne ignorowane przez Git. |

## Ścieżki kanoniczne i dane historyczne

Kod produkcyjny i testy korzystają wyłącznie z pakietów `core.*`, `models.*`, `services.*`, `gui.*`, `movement.*`, `simulation.*`, `scenarios.*` i `preview.*`. Dawne shimy importów oraz entrypoint `carla_manager.py` zostały usunięte w etapie 4B.10.

Wyjątkiem jest zgodność danych użytkownika: generator map nadal potrafi odczytać starszy cache `images/maps/generated/`. Nie jest to ścieżka importu i dane z tego katalogu nie są automatycznie usuwane ani przenoszone.

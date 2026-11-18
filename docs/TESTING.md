# Testowanie i weryfikacja

## Status

Projekt jest przed końcowymi manualnymi testami End-to-End. Podczas wstępnej
kontroli wykonano podstawowe operacje na Windows 11 z CARLA 0.9.16 oraz w Ubuntu
26.04.1 WSL2/WSLg z GUI łączącym się z serwerem Windows CARLA 0.9.16. W WSL2
sprawdzono m.in. Sensor Preview, Movement Control, Configuration Save/Load,
Disconnect/Reconnect i synchronizację dwóch klientów po zmianie mapy.

Nie był to pełny E2E ani test natywnego Linuxa. Bieżący przegląd dokumentacji
nie uruchamiał pełnego zestawu `unittest`, serwera CARLA ani GUI. Liczniki
33/316 w dokumentach pomocniczych dotyczą historycznego snapshotu; nie należy
ich przedstawiać jako aktualnego wyniku regresji.

## Framework i organizacja

Projekt używa standardowego `unittest` oraz `unittest.mock`. Katalog:

```text
tests/
├── actors/       # spawn, Ego, ownership, Change Model, ghost/static
├── connection/   # connect/disconnect/reconnect i reset stanu
├── core/         # resolver PythonAPI, services/runtime composition, store
├── movement/     # manual, TM, BasicAgent, walker, destination, preview
├── scenarios/    # capture, serialization, validation, preflight, clear, restore
├── sensors/      # attachment i lifecycle listenerów
├── simulation/   # settings, tick scheduler i dashboard
└── world/        # mapy, synchronizacja i External World Refresh
```

Testy używają fake worlds/actors/blueprints, `SimpleNamespace`, mocków kolejek i
zegara. Część jest jednostkowa, część komponentowa, ponieważ łączy kilka modułów
bez prawdziwego serwera. Nie istnieje automatyczny katalog live/integration.

## Uruchamianie

Polecenia należy wykonywać interpreterem skonfigurowanym dla właściwej wersji
CARLA API:

```powershell
python -m unittest discover -s tests -t . -v
python -m unittest discover -s tests/world -t . -v
python -m unittest tests.world.test_external_world_refresh -v
```

W tym zadaniu celowo nie wykonano pełnego discovery. Wymagane pakiety wynikają
z importów aplikacji, m.in. `carla`, Pillow, NumPy, Matplotlib, PyYAML i Tkinter.
Pygame jest importowany leniwie w procesach podglądu i zwykłe testy z mockami nie
otwierają okien.

## Co jest testowane mockami

- tworzenie klienta i publikacja błędu połączenia;
- routing `CarlaController` do services i trzech runtime mixinów;
- synchronizacja Managed/External i ochrona `world_id/world_generation`;
- map names 0.9.15/0.9.16, świeży world po `load_world()` i błędy etapów;
- spawn, physics, Ego, unikalne logical names i Change Model;
- attachment type, parent cleanup i późne callbacki sensorów;
- `WorldSettings`, lokalny tick scheduler i Dashboard;
- Manual/Free Roam/Destination, BasicAgent settings, Adaptive Speed i Walker AI;
- Exclusive/Shared Traffic Manager, port ownership i lifecycle;
- JSON/YAML, atomowy zapis, schema validation, preflight, Replace, dependencies i
  round trip Save→Load→Save;
- External World Refresh z nowym episode, reloadem tej samej mapy, kolejną
  zmianą podczas snapshotu oraz odrzuceniem starego raportu GUI.

Mocki potwierdzają kontrakt kodu, nie zachowanie Unreal Engine, sieci, renderera,
NavMesh, prawdziwego TM ani timing libcarla.

## Testy regresyjne

Szczególnie ważne pliki regresyjne:

- `tests/world/test_external_world_refresh.py` — hotfix zewnętrznej zmiany mapy;
- `tests/world/test_map_compatibility.py` — pełne/krótkie nazwy map i świeży world;
- `tests/world/test_world_synchronization.py` — ownership i inventory;
- `tests/actors/test_ego_vehicles.py` — wiele Ego;
- `tests/actors/test_actor_management_regressions.py` i
  `test_ghost_and_static_actor_regressions.py`;
- `tests/sensors/test_sensor_attachment_types.py` i `test_sensor_lifecycle.py`;
- `tests/movement/test_traffic_manager_lifecycle.py`,
  `test_movement_world_transition.py` i
  `test_vehicle_destination_navigation.py`;
- `tests/scenarios/test_scenario_roundtrip_regressions.py`,
  `test_scenario_path_and_restore_regressions.py`, preflight/restoration/clear;
- `tests/connection/test_connection_lifecycle.py`.

## Manualne E2E

Aktualna checklista znajduje się w
[testing/MANUAL_E2E_CHECKLIST.md](testing/MANUAL_E2E_CHECKLIST.md). Wszystkie
pozycje są niezaznaczone i dokument jawnie mówi, że nie został wykonany. Starszy,
szerszy katalog 107 przypadków dla obu wersji znajduje się w
`implementation/E2E_TEST_CATALOG.md`; zawiera także historyczne `NEEDS_REVIEW`,
które zostały rozstrzygnięte w kodzie (np. wiele Ego, EmptyMap, dynamiczna
pogoda, attachment). Nie należy kopiować z niego statusów.

Repozytorium nie zawiera pliku `.xlsx` ani `.xls`. Jeżeli arkusz wyników jest
przechowywany poza repozytorium, nie został zweryfikowany w tym przeglądzie.

## Macierz środowisk E2E

| Wymiar | Wymagane przebiegi | Status |
|---|---|---|
| CARLA | 0.9.15 i 0.9.16, zgodny Python API | podstawowo sprawdzone 0.9.16; 0.9.15 oczekuje |
| Python | zgodny z wheel danej wersji CARLA | Python 3.12.16 sprawdzony w WSL2 z 0.9.16 |
| Sieć | localhost i LAN/WSL2 | WSL2–Windows wstępnie sprawdzone; pozostałe oczekują |
| Klienci | jeden GUI, dwa GUI, GUI + klient techniczny | dwa GUI wstępnie sprawdzone w WSL2/Windows |
| World mode | asynchronous i synchronous | oczekujące |
| Rendering | normal i wybrane `no_rendering_mode` | oczekujące |

Nie należy mieszać klienta PythonAPI jednej wersji z serwerem drugiej bez
osobnego, świadomego testu — podstawowa macierz zakłada zgodne pary.

## Priorytetowe przebiegi

### Connection i lifecycle

- poprawny/błędny host, timeout, Disconnect/Reconnect;
- utrata serwera przy idle i podczas operacji;
- zamknięcie aplikacji z aktywnymi procesami/listenerami;
- brak stale actor/world proxies po reconnect.

### CARLA 0.9.15/0.9.16

- kolejne map changes z krótkimi i pełnymi identyfikatorami;
- spawn pieszego i fallback Z +2 m;
- dynamiczna lista pogody i dostępność AttachmentType;
- Save/Load z wersją serwera i pełną nazwą mapy;
- round trip aktorów pozostających Managed po Load i Refresh.

### External World Refresh

- drugi klient: Town01→Town02 i reload Town01→Town01;
- aktywne Manual, Destination, Walker AI, sensor/Movement/Spectator preview;
- nowy inventory jako External, brak reused-ID adoption;
- błąd/timeout snapshotu bez częściowej instalacji;
- Shared TM nieotrzymujący shutdown oraz Exclusive TM na własnym porcie.

### Configuration Management

- JSON i YAML, ta sama i inna mapa;
- pogoda, settings, wiele Ego, physics i attachment round trip;
- uszkodzony plik, brak mapy/blueprintu/attachment i stale preflight;
- Replace z Managed/External/Internal/infrastrukturą;
- Clear Managed i Entire World na izolowanym serwerze;
- częściowe niepowodzenie bez fałszywego sukcesu.

### Movement i multi-client

- wszystkie przejścia trybów vehicle/walker;
- BasicAgent z/bez Adaptive Speed, cel, stall i przeszkody;
- zamknięcie okna przy trwającym Destination;
- dwa porty Exclusive i kontrolowany konflikt jednego portu;
- Shared TM 8000 używany przez innego klienta;
- jeden tick master w synchronous mode.

## Kryteria poprawności

- GUI nie blokuje się na RPC i nie jest aktualizowane z obcego wątku;
- operacja kończy się jednoznacznym success/partial/failure, bez fałszywego OK;
- po world transition nie są używane stare Actor/World/Blueprint references;
- ownership nie zmienia się bez utworzenia, Load albo jawnej adopcji;
- cleanup zatrzymuje lokalne listenery, kamery, procesy i control runtime;
- Shared TM i aktorzy innych klientów nie są niejawnie zatrzymywani/przejmowani;
- zapis konfiguracji zawiera tylko Managed scope i jest ponownie ładowalny;
- różnice v15/v16 są raportowane jako konkretna brakująca mapa/API/blueprint,
  nie jako ogólna deklaracja kompatybilności.

## Ograniczenia weryfikacji

- brak pomiaru code coverage; liczba testów nie jest procentem pokrycia;
- część testów wywołuje prywatne metody i jest sprzężona z implementacją;
- duże moduły scenariuszy współdzielą fake'i między plikami;
- brak automatycznych testów rzeczywistego CARLA, GPU, LAN i Pygame;
- raporty wcześniejszych celowanych przebiegów powstały dla konkretnych
  snapshotów i nie zastępują świeżej regresji;
- nie deklaruje się deterministyczności scenariusza ani nawigacji.

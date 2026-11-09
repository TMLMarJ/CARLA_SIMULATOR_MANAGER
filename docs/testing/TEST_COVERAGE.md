# Audyt pokrycia funkcjonalnego testów

## Metoda audytu

Zestawienie powstało przez analizę 33 plików i 316 unikalnych metod testowych. Nie uruchamiano narzędzia do pomiaru pokrycia, dlatego dokument nie podaje procentów. Priorytet opisuje ryzyko regresji, a nie liczbę istniejących testów. Trzy usunięte przypadki sprawdzały wyłącznie zgodność nieistniejących już shimów; końcowa regresja 4B.10 wykonała pozostałe 316 testów z wynikiem `OK`.

## Zestawienie

### Stabilizacja po refaktoryzacji — etap A

Celowane testy bez serwera CARLA obejmują teraz dodatkowo:

- budowę baseline infrastruktury przed pierwszą synchronizacją Connect oraz
  ponowne zbudowanie go przez nowy kontroler po Reconnect;
- synchronizację pustego i mieszanego świata, aktorów EXTERNAL, sensorów oraz
  statycznej infrastruktury mapy bez list specyficznych dla Town02;
- ochronę baseline infrastruktury w Clear Entire World oraz zachowanie stanów
  sterowania aktorów, których zniszczenie zakończyło się błędem;
- Change World z aktywnym autopilotem Free Roam/Destination, Walker AI,
  otwartym Movement Preview i nieistniejącym już aktorem;
- wykonanie cleanupu CARLA przez worker oraz odświeżenie pogody po zmianie
  mapy wyłącznie z cache `DataStorage`, bez RPC z callbacku Tkinter.

Zestaw celowany dla tego etapu wykonał 77 testów z wynikiem `OK`. Nie
uruchamiano pełnej regresji ani serwera CARLA.

| Obszar | Istniejące testy | Najważniejsze sprawdzane zachowania | Najważniejsze braki | Priorytet |
|---|---|---|---|---|
| Connection i lifecycle | `connection/test_connection_lifecycle.py` oraz przypadki w simulation, movement i scenarios | utworzenie kontrolera dla wybranego endpointu, blokada reconnect przy aktywnym Spectator View, błąd połączenia, reset i rebind `DataStorage`, idempotentne żądanie shutdownu, stale events po zmianie świata | automatyczny reconnect end-to-end, błąd w konstruktorze/start kontrolera, rzeczywiste zakończenie thread/process i utrata połączenia sieciowego | **wysoki** |
| Controller, dispatcher, runtime i usługi | `core/test_controller_services.py`, `core/test_controller_runtimes.py`, `core/test_application_architecture.py` oraz testy funkcjonalne | kanoniczny entrypoint GUI, callback Spectatora bez zależności od Managera, importowalne targety preview, priorytet i FIFO, propagacja wyjątku, synchroniczna delegacja oraz komplet routingu runtime | pełny change-world success/failure/recovery w jednym teście, błąd każdej usługi podczas pracy prawdziwego workera i pomiar braku równoległego RPC z CARLA | **wysoki** |
| World & Weather i Map Preview | `world/test_map_preview.py`, `world/test_world_synchronization.py`, `actors/test_ghost_and_static_actor_regressions.py` | geometria i cache mapy, synchronizacja external/managed, zmiana epizodu, infrastruktura statyczna | brak workflow `change_world` success/failure/recovery jako całości, aplikacji pogody i błędów kolejki map preview; brak testów unieważnienia wszystkich referencji po map change | **wysoki** |
| Actors i ownership | pięć plików w `actors/` | spawn i dokładny blueprint, zmiana modelu/fizyki, ghost, static infrastructure, adoption, ownership guards, spawn points | rollback przy błędzie zastąpienia aktora z sensorami, cleanup zależności po błędzie, równoległa zmiana aktora przez obcego klienta, kompletne usuwanie stanu ruchu/sensorów | **wysoki** |
| Sensors | `sensors/test_sensor_lifecycle.py` oraz przypadki w actors, scenarios, simulation i movement | kaskadowe stop/destroy/unregister sensorów rodzica, cleanup rejestru mimo błędu `destroy()`, zatrzymanie aktywnych preview, późny callback zamkniętego bufora zdarzeń, ownership guard, nazwa/status i zapis/odtwarzanie | attach i transform po stronie pętli kontrolera, dekodery obrazu/value/spatial, zamykanie okien preview, cleanup przy map change/disconnect z prawdziwymi aktorami CARLA | **wysoki** |
| Movement Control | siedem plików w `movement/` | manual/autopilot, walker random/manual/destination, route planner, arrival/stuck, watchdog, kamera, stale world commands | lifecycle prawdziwego procesu IPC, wymuszone zakończenie procesu, awaria sensora/aktora podczas sesji, kontrakt Traffic Manager/GlobalRoutePlanner z realnym CARLA API | **wysoki** |
| Simulation Control i Dashboard | cztery pliki w `simulation/` | walidacja settings, maszyna stanów, scheduler, priorytet Pause/Stop, snapshoty i prezentacja dashboardu | konflikt ustawień z operacją scenariusza, zewnętrzny tick podczas przełączenia kontrolera, pełny przepływ kolejka→GUI, błędy `world.apply_settings()` w kontrolerze | **średni** |
| Scenario Management | osiem plików w `scenarios/` | JSON/YAML, walidacja, atomic write, capture scope, preflight, stale plan, selective clear, restoration, partial success, GUI workflow i roundtrip | rollback po częściowej destrukcji, disconnect w każdej fazie Save/Load/Clear, zmiana świata przez zewnętrznego klienta w trakcie wykonania, awarie systemu plików poza writerem | **wysoki** |
| DataStorage i komunikacja | `core/test_state_store_migration.py` oraz testy connection, world, actors, sensors, movement, simulation i scenarios | tożsamość klas starych/nowych importów, kanoniczne rejestry, zachowanie żywych referencji i modeli, brakujące ID, tworzenie stanów aktora, synchronizacja, ownership, stale world IDs oraz reset/rebind po disconnect | brak samodzielnych testów współbieżnego użycia blokady, spójności snapshotów, pełnych schematów payloadów kolejek, kolejkowania priorytetowego poza symulacją i odporności na nieznane zdarzenia | **wysoki** |

## Poziomy wymaganej weryfikacji

| Obszar | Pokrycie jednostkowe / komponentowe bez serwera | Wymagane testy integracyjne z CARLA | Wymagana weryfikacja manualna |
|---|---|---|---|
| Connection | logika connect/error/disconnect, reset stanu i pojedyncze żądanie cleanupu | utrata TCP, timeout, reconnect do nowego epizodu, faktyczne zakończenie wątku kontrolera | komunikaty i dostępność kontrolek podczas długiego reconnectu |
| World i actors | synchronizacja managed/external, ownership, stale world ID, część map change | `load_world()`, unieważnienie uchwytów aktorów i sensorów, częściowe błędy destroy/spawn | płynność GUI i poprawność tabel po dużym świecie |
| Sensors | rejestry, ownership guards, kaskadowy cleanup rodzica i preview IDs | attach/remove/transform oraz callbacki na rzeczywistych sensorach | okna camera/value/event/spatial preview i ich zamykanie przy disconnect/map change |
| Movement | maszyny stanów i kolejki z fake'ami | proces Pygame, Traffic Manager i GlobalRoutePlanner z wersją CARLA używaną przez projekt | sterowanie klawiaturą, fokus okna i awaryjne zamknięcie podglądu |
| Simulation | scheduler i priorytety poleceń | synchroniczne/asynchroniczne ticki przeciw prawdziwemu `world` | zachowanie UI przy wolnym ticku i zewnętrznym kliencie |
| Scenarios | serializer, preflight, selective clear, restore i partial success | Save/Load/Clear z prawdziwymi blueprintami, map change i awarią w kolejnych fazach | zgodność odtworzonej sceny, pogody i sensorów w symulatorze |

Istnienie testu jednostkowego nie oznacza pełnego pokrycia obszaru. Szczególnie lifecycle zasobów CARLA wymaga dodatkowo testów integracyjnych, a zachowanie okien i sterowania — testów manualnych.

## Testy jednostkowe a integracyjne

Większość testów jest jednostkowa albo komponentowa z fake'ami. Nie znaleziono testu tworzącego `carla.Client`, prawdziwy root Tkinter ani proces Pygame. Żaden test nie wymaga działającego serwera CARLA.

Za komponentowe należy uznać przede wszystkim:

- `test_scenario_restoration.py`;
- `test_scenario_roundtrip_regressions.py`;
- końcowy zapis w `test_scenario_serialization.py`;
- część `test_world_cleaner.py` i `test_simulation_control.py`;
- testy przejścia świata w `movement/`.

Są to integracje klas wewnątrz procesu, nie integracje z zewnętrznym serwerem.

## Duplikaty i testy historyczne

Przed reorganizacją discovery zgłaszało 313 przypadków. W kodzie znajdowało się 281 unikalnych metod `test_*`. Różnicę 32 powodował import `WalkerControlTests` do dwóch modułów ruchu; loader uruchamiał wszystkie 16 metod tej klasy dodatkowo w każdym module. Import został zmieniony na import modułu pomocniczego, bez usuwania unikalnych testów.

Nie usunięto innych podobnych testów. Przypadki dotyczące Save/Load, ownership i map change częściowo się pokrywają, ale używają innych warunków brzegowych i chronią odrębne regresje.

Nazwy plików oparte na historii implementacji (`stage`, `fix`, `hotfix`) zastąpiono nazwami funkcjonalnymi. Zachowanie testów pozostało bez zmian.

## Testy zależne od implementacji

Najsilniej związane z wewnętrzną strukturą są testy, które:

- wywołują prywatne metody `_service_*`, `_perform_*` i `_handle_*` kontrolera;
- konstruują `Manager`, widoki lub kontroler przez `__new__` i ręcznie wypełniają pola;
- sprawdzają dokładny rozmiar kolejki lub kolejność wewnętrznych wywołań;
- używają `inspect.getsource()` do sprawdzania sposobu importu dialogów;
- importują fake'i z innych modułów testowych.

Testy te są wartościowe przed refaktoryzacją, ale mogą wymagać migracji do testów kontraktowych, gdy w etapie 4 zmienią się granice modułów.

## Rekomendowany zakres etapu 3B

1. Rozszerzyć testy connection o automatyczny reconnect i błędy startu kontrolera.
2. Rozszerzyć testy sensor preview buffers o dekodery image/value/spatial oraz attach/transform bez prawdziwego GUI.
3. Wydzielić wspólne fake'i scenariuszy z plików `test_*` do małych modułów pomocniczych.
4. Rozdzielić duże pliki `test_scenario_serialization.py` i `test_world_cleaner.py` według odpowiedzialności.
5. Wprowadzić fabryki `FakeWorld`, `FakeActor`, `FakeSensor` z jawnymi kontraktami, zamiast wielu niezgodnych wariantów.
6. Udokumentować przyszły, opcjonalny poziom live integration z CARLA, domyślnie wyłączony.
7. Dopiero po ustabilizowaniu struktury zmierzyć pokrycie narzędziem coverage i ustalić cele liczbowe.

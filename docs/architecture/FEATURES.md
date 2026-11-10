# Architektura funkcjonalności

## Main View i połączenie

**Moduły i klasy:** `gui.application.Manager`, `gui.main_view.MainView`, `gui.tabs.home.CustomMainViewFrame`, `ConnectionFrame`, `SpectatorFrame`, `gui.widgets.dashboard.SimulationDashboardFrame`.

`Manager` jest rootem Tkinter i właścicielem bieżącego `DataStorage`, `CarlaController`, `SpectatorPreviewManager` i `MovementControlSessionManager`. Main View zawiera formularz host/port, kontrolki Spectator oraz dashboard. Po połączeniu `MainView.initialize_connected_views()` dynamicznie tworzy pozostałe zakładki; po rozłączeniu `clear_connected_views()` je niszczy.

Operacje asynchroniczne wracają przez kolejki. `check_connection()`, `check_simulation_events()`, `_check_scenario_events()` i `check_spectator_preview()` są cyklicznymi callbackami `after`.

## World & Weather

**Moduły i klasy:** `gui.tabs.world.CustomWorldAndWeatherFrame`, `CarlaController.change_world()`, `change_weather()`, `preview.map.MapPreviewCache`.

Zakładka pobiera listę map, pogodę i spawn pointy z `DataStorage`. Zmiana pogody jest kolejką polecenia. Zmiana mapy uruchamia workflow `Manager.begin_world_change()`, zatrzymuje funkcje zależne od epizodu i czeka na wynik kontrolera. Widok odczytuje wygenerowany PNG z cache `resources/maps/generated/` (z fallbackiem do starego `images/maps/generated/`); nie generuje geometrii ani nie wykonuje CARLA RPC.

Powiązania: zmiana świata resetuje aktorów, sensory, ruch, podglądy, dashboard, ustawienia symulacji oraz plany scenariuszy.

## Actors: Vehicles, Pedestrians, Static Objects

**Moduły i klasy:**

- `gui.tabs.vehicles.CustomVehicleFrame`, `VehicleTable`;
- `gui.tabs.pedestrians.CustomPedestrianFrame`, `ActorTable`;
- `gui.tabs.static_objects.CustomStaticObjectFrame`, `StaticObjectTable`;
- `gui.widgets.actor_preview.GhostPreview`, `ActorPreviewFrame`;
- `gui.windows.actor_editor.EntityEditWindow` i pomocnicze tabele;
- `gui.windows.model_picker.ChangeModelTop`, `gui.windows.transform_editor.ChangeTransformTop`;
- `actors.ActorInfo`, `Actors`.

Widoki obsługują wybór blueprintu, transformację, ghost preview, spawn, edycję, zmianę modelu i usuwanie. Pojazdy dodatkowo edytują `VehiclePhysicsControl`. Publiczne metody kontrolera sprawdzają ownership przed mutacją; aktor external musi najpierw przejść Take Ownership.

Kontroler weryfikuje, czy CARLA zwróciła dokładnie żądany blueprint. Przy fizyce stosuje ustawienia, odczytuje je ponownie i porównuje pola. Wyniki trafiają przez `actor_event_queue`, a tabele odświeżają się w GUI.

Ghost preview jest aktorem technicznym oraz kamerą RGB tworzonymi przez kontroler. Callback zapisuje ostatni obraz, a Tkinter odświeża podgląd co 100 ms. Ghost jest wykluczany z synchronizacji i scenariuszy jako internal preview.

## Sensors i podgląd danych

**Moduły i klasy:** `gui.tabs.sensors.CustomSensorFrame`, `AddSensorTop`, `ChangeSensorTransformTop`, bufory z `preview.sensor_buffers`, okna z `gui.windows.sensor_preview`, `actors.SensorInfo`.

Funkcje obejmują:

- utworzenie sensora z parent actor, blueprintem, attachment i transformacją względną;
- zmianę logicznej nazwy oraz transformacji;
- usunięcie sensora;
- podgląd kamer/DVS/segmentacji, wartości, zdarzeń i danych przestrzennych.

Kontroler uruchamia `sensor.listen()` i śledzi aktywne preview IDs. Callback nie dotyka Tkinter; zapisuje dane do thread-safe buffer. Okno `*PreviewTop` pobiera snapshot przez `after`. Zmiana mapy, usunięcie rodzica i rozłączenie zatrzymują stream oraz niszczą sensory techniczne.

Sensor w scenariuszu zależy od parent `scenario_id`; podczas Load rodzic musi zostać odtworzony wcześniej.

## Movement Control

**Moduły i klasy:** `movement.session.MovementControlSessionManager`, `gui.windows.movement_control.MovementControlWindow`, `DestinationMapWindow`, `gui.widgets.navigation_map.NavigationMap`, `preview.movement.MovementPreviewProcess`, `ManualControlSmoother`, funkcje z `movement.destination`, `movement.vehicle` i `movement.walker`.

Menedżer utrzymuje jedną aktywną sesję, tworzy okno przez fabrykę przekazaną z `gui.application.Manager` oraz uruchamia proces Pygame. Przekazuje żądania do `CarlaController` i odpytuje `movement_event_queue`; nie importuje konkretnej klasy okna.

### Pojazdy

Kontroler obsługuje No Control, Manual Control, Autopilot — Free Roam oraz
Autopilot — Destination. Sterowanie manualne jest odświeżane w pętli
kontrolera z ograniczeniem częstotliwości i timeoutem wejścia. Free Roam
korzysta z aplikacyjnego Traffic Managera, domyślnie na zarezerwowanym porcie
`8050`.

Destination korzysta z `GlobalRoutePlanner.trace_route()`. Worker zachowuje
surowy plan `(carla.Waypoint, RoadOption)[]`, a do GUI publikuje wyłącznie
serializowane snapshoty i geometrię minimapy. Domyślny executor `basic_agent`:

1. tworzy oficjalny `BasicAgent` z tej samej instalacji CARLA 0.9.16;
2. przekazuje surowy plan przez `set_global_plan()`;
3. wyłącza autopilot TM dla tego pojazdu;
4. wykonuje `run_step()` i `apply_control()` w workerze kontrolera — raz na
   klatkę sterowaną przez aplikację w trybie sync albo około 20 Hz w async;
5. pozostawia wspólnemu monitorowi Destination kontrolę postępu, odchylenia,
   dotarcia i utknięcia.

Executory `path` i `route` są zachowane diagnostycznie i są wybierane wyłącznie
przez `CARLA_MANAGER_DESTINATION_EXECUTOR`. Nie sterują pojazdem jednocześnie
z BasicAgent.

W GUI dla BasicAgent dostępne są **Maximum speed** (10–90 km/h, domyślnie
40 km/h) i **Adaptive speed**. Dane przechodzą ścieżką GUI → session manager →
kolejka kontrolera → worker. Są przechowywane per actor/runtime, mają
pierwszeństwo przed fallbackami środowiskowymi i są blokowane na czas aktywnego
przejazdu. Adaptacja przygotowuje profil z geometrii planu raz przy aktywacji,
a podczas jazdy zmienia publiczne `agent.set_target_speed()`; nie modyfikuje
PID ani awaryjnego hamowania BasicAgent.

Zamknięcie Movement Control nie przerywa aktywnego Destination. Ponowne
otwarcie odtwarza ustawienia i blokadę z aktualnego stanu runtime. Runtime
kończy zmiana trybu, wynik terminalny, błąd agenta, usunięcie aktora,
disconnect, Clear World albo zmiana mapy.

### Piesi

Obsługiwane są tryby stop/manual/destination oraz AI walker controller. Kontroler tworzy lub niszczy `controller.ai.walker`, zapisuje cel i prędkość, monitoruje postęp i ma fallback bez kontrolera AI.

### Cel i podgląd

Cel może być wpisany jako XYZ lub wybrany na obrazie mapy OpenDRIVE generowanym i cache'owanym przez `MapPreviewGenerator`. Współdzielony widget ma tryb compact w oknie Movement Control oraz interactive z zoomem wokół kursora, pan i wyborem konkretnego waypointu `Driving`. Przy wielu pobliskich pasach użytkownik wybiera `road_id/lane_id/section_id` i widzi kierunek waypointu. Walidacja odbywa się w kontrolerze z użyciem danych CARLA, a nie w Tkinter.

Minimapa otrzymuje co około 250 ms lekki snapshot pozycji, yaw, statusu, destination i zaplanowanej trasy przez `movement_event_queue`. Worker jako jedyny odczytuje transform aktora. Obraz bazowy nie jest renderowany ponownie przy aktualizacji pozycji; GUI przesuwa tylko obraz cache i aktualizuje markery oraz linię waypointów trasy. Movement Preview tworzy w CARLA kamerę przypiętą do aktora, lecz proces Pygame otrzymuje tylko ramki i zwraca wejście; nie ma bezpośredniego klienta CARLA.

Zmiana świata, usunięcie aktora, Load/Clear i rozłączenie kończą runtime i
unieważniają referencje poprzedniego epizodu. Samo zamknięcie okna kończy
podgląd i sesję GUI, ale zgodnie z kontraktem nie anuluje już uruchomionego
Destination.

### Ograniczenia Movement Control

- Adaptive Speed jest eksperymentalną heurystyką geometrii 2D. Nie uwzględnia
  pogody, przyczepności, masy pojazdu ani przechylenia drogi.
- BasicAgent zachowuje ograniczenia CARLA 0.9.16 w wykrywaniu pieszych i
  przeszkód statycznych; GUI nie deklaruje pełnej ochrony przed nimi.
- Executor `route` nie gwarantuje zaplanowanych zmian pasa, a `path` pozostaje
  mniej niezawodny na części skrętów; oba służą głównie diagnostyce.
- Aktualny build klienta CARLA nie udostępnia RSS, dlatego aplikacja nie
  oferuje podglądu ani funkcji RSS.

## Simulation Control

**Moduły i klasy:** `gui.tabs.simulation.SimulationControlFrame`, `simulation.manager.SimulationManager`, metody `CarlaController._service_simulation()` i `_handle_simulation_control_command()`.

GUI odczytuje i stosuje:

- `synchronous_mode`;
- `fixed_delta_seconds`;
- `no_rendering_mode`;
- `substepping`;
- `max_substep_delta_time`;
- `max_substeps`.

`SimulationManager` waliduje kombinację pól, stosuje `WorldSettings` i potwierdza wartości odczytem z CARLA. Maszyna stanów rozróżnia tryb asynchroniczny, synchronizację kontrolowaną zewnętrznie, GUI paused/running/stepping oraz Error.

Gdy GUI jest kontrolerem ticków, pętla `CarlaController` wywołuje `world.tick(TICK_TIMEOUT_SECONDS=5.0)`. Polecenie Pause i Stop korzysta z kolejki priorytetowej. W trybie zewnętrznym kontroler obserwuje `world.get_snapshot()` bez wywoływania ticka.

## Dashboard

**Moduły i klasy:** `simulation.dashboard.SimulationDashboardMonitor`, `gui.widgets.dashboard.SimulationDashboardFrame`.

Monitor okresowo buduje:

- snapshot symulacji i świata;
- statystyki aktorów według kategorii i ownership;
- statystyki sensorów, w tym streaming i błędy;
- status aplikacji, kolejki, viewer i ostatni błąd.

Wyniki są słownikami przechowywanymi w `DataStorage`; widok renderuje je bez bezpośrednich zapytań do CARLA. Dashboard pokazuje external actors i pozwala rozpocząć Take Ownership. Adoption jest wykonywane przez kontroler po sprawdzeniu runtime identity oraz konfliktów z operacjami scenariusza.

## Scenario Management

**Moduły i klasy:** `ScenarioManager`, `ScenarioCapture`, `ScenarioSerializer`, `ScenarioValidator`, `ScenarioLoader`, `ScenarioRestorer`, `WorldCleaner`, `WorldSynchronizer`, `ActorAdopter` oraz modele z `models.scenarios`. Implementacje domenowe znajdują się w `scenarios.*`.

`ScenarioSaveFrame` jest częścią zakładki Simulation Control. Save najpierw synchronizuje świat, zapisuje managed actors, waliduje dokument i wykonuje atomowy zapis JSON/YAML. Load wykonuje bezinwazyjny preflight, pokazuje zakres użytkownikowi, a po potwierdzeniu ponownie weryfikuje plik i świat przed selective clear lub zmianą mapy.

Clear World ma plan i token. Tryb managed usuwa aktorów zarządzanych, a szerszy tryb/tryb scenariusza korzysta z klasyfikacji oraz zabezpieczeń infrastruktury i aktorów wewnętrznych. `WorldCleaner` sprawdza, czy plan nie stał się nieaktualny, i niszczy zależności w odpowiedniej kolejności.

`scenario_operation_lock` oraz flagi kontrolera blokują równoległe Save/Load/Clear/Adoption i część innych mutacji.

Szczegółowy przepływ pliku opisuje [DATA_FLOW.md](DATA_FLOW.md).

## Spectator Preview

**Moduły i klasy:** `preview.spectator.SpectatorPreviewManager`, `_LatestRgbFrame`, `run_spectator_preview`; funkcje w `preview.spectator_controls`; `gui.tabs.home.SpectatorFrame`.

Funkcja uruchamia osobne okno Pygame w procesie `spawn`. Proces:

1. tworzy własny `carla.Client` z hostem i portem głównego połączenia;
2. pobiera spectator i tworzy kamerę RGB;
3. przetwarza klawiaturę/mysz oraz ustawia transformację spectator i kamery;
4. renderuje ostatnią ramkę i overlay;
5. okresowo porównuje `world.id` i kończy się po zmianie mapy.

Manager odpytuje kolejkę IPC i aktualizuje `SpectatorFrame`. Przed zmianą świata lub rozłączeniem prosi proces o zatrzymanie; przy timeout może użyć `terminate()`/`kill()`.

## Map Preview Generator

**Moduły i klasy:** `preview.map.MapPreviewGenerator`, `MapPreviewCache`, `gui.tabs.world.CustomWorldAndWeatherFrame`, metody kontrolera `_queue_map_preview_generation()` i `_generate_map_preview()`.

Po połączeniu i zmianie mapy kontroler kolejkuje generowanie. Operacja działa w tym samym wątku co inne polecenia CARLA:

- `carla.Map.generate_waypoints(2.5)` dostarcza geometrię;
- waypointy są grupowane według `(road_id, lane_id)` i dzielone przy dużych przerwach;
- Pillow renderuje obraz 800×600 z zachowaniem proporcji;
- `MapPreviewCache` zapisuje zweryfikowany PNG do `resources/maps/generated/` pod nazwą zawierającą hash mapy i może odczytać starszy cache z `images/maps/generated/`.

Zlecenie zawiera `world_id` i nazwę mapy, więc wynik starego epizodu jest odrzucany. `shutdown_event` może przerwać generowanie. GUI otrzymuje jedynie ścieżkę przez `map_preview_event_queue` i ładuje obraz z dysku.

## Powiązania przekrojowe

| Zdarzenie | Funkcjonalności wymagające reakcji |
|---|---|
| Zmiana mapy | Movement, Spectator, sensory, ghost, scenario plans, dashboard, simulation control, map preview |
| Usunięcie aktora | sensory potomne, movement session, rejestry i dashboard |
| Scenario Load/Clear | blokada mutacji, zamknięcie podglądów, selective clear, synchronizacja i odświeżenie GUI |
| Rozłączenie | wszystkie zasoby CARLA, procesy podglądu, widoki połączeniowe i `DataStorage` |
| External actor adoption | rejestr ownership, stan ruchu, tabele Actors i dashboard |

Architektura jest funkcjonalnie kompletna w kodzie, ale stopień poprawności w działającym CARLA nie był w ramach tej dokumentacji weryfikowany uruchomieniowo.

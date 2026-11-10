# Manualne testy end-to-end

## Cel i sposób użycia

Checklista obejmuje końcową weryfikację CARLA Simulator Manager z prawdziwym
serwerem CARLA 0.9.16. Nie została jeszcze wykonana. Dla każdego przypadku
należy zapisać datę, mapę, tryb sync/async, wynik `PASS/FAIL/BLOCKED`, log oraz
krótką informację umożliwiającą odtworzenie błędu.

Raporty etapowe nie zastępują tej checklisty. Zalecane minimum map to Town01,
Town03, Town07 i Town15; długie ładowanie Town01 → Town15 jest osobnym testem
lifecycle.

## Przygotowanie

- [ ] E2E-000 — Uruchomić CARLA 0.9.16 i aplikację bez
  `CARLA_MANAGER_DESTINATION_EXECUTOR`; potwierdzić brak błędu resolvera i
  domyślny BasicAgent.
- [ ] E2E-001 — Zapisać wersję serwera, klienta PythonAPI, interpretera,
  systemu, GPU i aktualny commit.
- [ ] E2E-002 — Potwierdzić, że port RPC jest osiągalny i port TM `8050` nie
  należy do innego procesu.
- [ ] E2E-003 — Powtórzyć start z adresem zdalnego serwera w Connection
  Managerze; aplikacja nie może zakładać `localhost` poza wartościami
  domyślnymi formularza.

## Connection Manager i lifecycle

- [ ] E2E-010 — Connect → Disconnect → Reconnect do tego samego serwera;
  sprawdzić odbudowę zakładek, store'a i dashboardu.
- [ ] E2E-011 — Błędny host, zamknięty port i timeout; GUI pozostaje
  responsywne i pozwala ponowić próbę.
- [ ] E2E-012 — Utrata serwera podczas bezczynności, następnie ponowne
  połączenie po restarcie serwera.
- [ ] E2E-013 — Disconnect podczas aktywnego Movement Preview, sensora i
  Destination; brak osieroconych procesów, callbacków i referencji aktorów.
- [ ] E2E-014 — Zamknięcie aplikacji po aktywnych podglądach; proces kończy się
  bez pozostawionych okien Pygame i workera.

## World & Weather

- [ ] E2E-020 — Zmienić pogodę i potwierdzić wynik w świecie oraz GUI.
- [ ] E2E-021 — Town01 → Town15 podczas aktywnego BasicAgent Destination;
  runtime agenta i sensory starego świata kończą się przed `load_world()`, GUI
  pozostaje responsywne, brak `_episode != nullptr`.
- [ ] E2E-022 — Zmiana mapy podczas Free Roam, Manual i Walker AI; po nowej
  mapie żaden stary actor ID ani plan nie jest używany.
- [ ] E2E-023 — Zmiana mapy z otwartym Movement Preview, Spectator Preview,
  podglądem sensora i ghost preview; wszystkie zasoby poprzedniego epizodu są
  zamknięte.
- [ ] E2E-024 — Po zmianie mapy utworzyć nowy pojazd, uruchomić Free Roam, a
  następnie BasicAgent Destination; TM i agent korzystają z nowego świata.
- [ ] E2E-025 — Powtórzyć zmianę mapy w trybie sync oraz async.

## Actors, ownership i Sensors

- [ ] E2E-030 — Spawn/Edit/Delete pojazdu, pieszego i obiektu statycznego;
  sprawdzić dokładny blueprint, transformację i odświeżenie tabel.
- [ ] E2E-031 — Utworzyć aktora drugim klientem, wykryć go jako external i
  potwierdzić blokadę mutacji przed Take Ownership.
- [ ] E2E-032 — Take Ownership external actor bez respawnu; następnie Movement
  Control, zapis scenariusza i usunięcie jako managed.
- [ ] E2E-033 — Usunąć aktywnie sterowany pojazd z drugiego klienta; aplikacja
  kończy runtime bez dalszego `run_step()`/TM RPC.
- [ ] E2E-034 — Attach, transform, preview i remove dla obsługiwanych sensorów
  image/value/event/spatial.
- [ ] E2E-035 — Usunąć rodzica aktywnego sensora oraz zmienić mapę z aktywnym
  preview; listener jest zatrzymany, a późny callback ignorowany.
- [ ] E2E-036 — Sensor RSS: aplikacja pokazuje kontrolowaną informację o braku
  RSS w aktualnym buildzie, bez deklarowania podglądu.

## Simulation Control i Dashboard

- [ ] E2E-040 — Async → sync, Pause/Resume, Single Step i powrót do async;
  dashboard pokazuje zgodny stan i ramki.
- [ ] E2E-041 — Zewnętrzny klient kontroluje ticki; aplikacja obserwuje świat
  bez wielokrotnego ticka tej samej klatki.
- [ ] E2E-042 — Dashboard poprawnie rozróżnia managed/external actors,
  sensory, kolejki i błędy po reconnect oraz zmianie mapy.

## Movement Control — przejścia trybów

- [ ] E2E-050 — No Control → Manual → Free Roam → Destination → Manual → No
  Control; w każdej granicy istnieje tylko jeden mechanizm sterowania.
- [ ] E2E-051 — BasicAgent → Free Roam: agent kończy cykl przed włączeniem
  autopilota TM; pojazd nie otrzymuje podwójnego sterowania.
- [ ] E2E-052 — BasicAgent → Manual: ostatni control agenta nie jest ponawiany,
  a wejście manualne działa natychmiast po potwierdzeniu zmiany.
- [ ] E2E-053 — BasicAgent → ponowne Destination: poprzedni agent i surowy plan
  są usunięte, nowy cel otrzymuje nowy plan.
- [ ] E2E-054 — Zamknąć Movement Control podczas Destination, otworzyć ponownie
  i potwierdzić trwającą jazdę, status oraz zablokowane ustawienia.
- [ ] E2E-055 — Usunąć aktora, wykonać Clear World oraz Disconnect podczas
  każdego trybu; brak późnych wywołań agenta i starego świata.

## Vehicle Destination — BasicAgent

- [ ] E2E-060 — Bez zmiennej executora wybrać krótki i długi cel; potwierdzić
  BasicAgent, poprawną trasę minimapy, światła i zatrzymanie w wybranym celu.
- [ ] E2E-061 — Trasy na Town01/Town02/Town07: skrzyżowania, ronda, zawracanie
  oraz długi przejazd bez utraty planu.
- [ ] E2E-062 — Droga wielopasmowa: wymagane zmiany pasa i zachowanie wybranego
  pasa docelowego.
- [ ] E2E-063 — Maximum speed 10, 40 i 90 km/h z wyłączoną adaptacją;
  potwierdzić zastosowanie wartości bez wpływu na światła i awaryjne hamowanie.
- [ ] E2E-064 — Maximum speed 40 km/h z Adaptive speed: długa prosta, ostry
  łuk i ciasne rondo; zwalnianie zaczyna się przed manewrem, a przyspieszenie
  wraca po wyjściu.
- [ ] E2E-065 — Zmiana pasa na prostym odcinku z Adaptive speed nie powoduje
  nieuzasadnionego gwałtownego hamowania.
- [ ] E2E-066 — Zamknąć/otworzyć okno podczas jazdy; Maximum speed i Adaptive
  speed pozostają niezmienione i zablokowane. Po Arrived/Cancel/Error są
  odblokowane.
- [ ] E2E-067 — Przeszkoda, wolniejszy pojazd i czerwone światło na trasie z
  łukiem; hamowanie BasicAgent ma pierwszeństwo przed profilem prędkości.
- [ ] E2E-068 — Błąd resolvera/agenta lub usunięcie pojazdu podczas jazdy;
  kontrolowane zakończenie nie pozostawia aktywnego sterowania.
- [ ] E2E-069 — Diagnostycznie uruchomić osobno `path` i `route`; potwierdzić,
  że ich wybór jest jawny, ustawienia BasicAgent są ukryte i nie zmieniono ich
  dotychczasowego działania. Nie traktować wyników jako kryterium domyślnego
  executora.

## Piesi

- [ ] E2E-070 — Walker Manual, AI/random i Destination oraz przejścia do stop.
- [ ] E2E-071 — Zmiana mapy, usunięcie pieszego i Disconnect podczas Walker AI
  lub Destination; kontroler i cel starego świata są usunięte.

## Scenario Save/Load/Clear

- [ ] E2E-080 — Save JSON i YAML z pogodą, managed actors, sensorami i stanem;
  external actors nie trafiają do zakresu bez przejęcia.
- [ ] E2E-081 — Preflight i Load na tej samej mapie; potwierdzić blueprinty,
  transformacje, parent sensorów, ownership i brak podwójnych aktorów.
- [ ] E2E-082 — Load wymagający zmiany mapy; lifecycle Movement/TM kończy się
  przed przejściem świata, a scenariusz jest odtworzony w nowym epizodzie.
- [ ] E2E-083 — Clear Managed Only nie usuwa external ani infrastruktury mapy.
- [ ] E2E-084 — Clear Entire World: zweryfikować ostrzeżenie, zakres i ochronę
  infrastruktury zgodnie z planem clear.
- [ ] E2E-085 — Nieaktualny preflight/clear plan po zmianie świata przez drugi
  klient jest odrzucony zamiast wykonany na nowym stanie.

## Dwóch klientów CARLA

- [ ] E2E-090 — Drugi klient tworzy, przesuwa i usuwa aktorów; Refresh World
  aktualizuje external records bez automatycznego przejęcia.
- [ ] E2E-091 — Drugi klient korzysta ze współdzielonego TM `8000`, aplikacja z
  własnego `8050`; aplikacja nie zatrzymuje ani nie rekonfiguruje obcego TM.
- [ ] E2E-092 — Dwie instancje Managera używają osobnych portów exclusive
  (`8050` i `8051`); Free Roam działa niezależnie, bez przejęcia portu.
- [ ] E2E-093 — Próba uruchomienia dwóch instancji na `8050` kończy się
  kontrolowanym błędem własności, bez dołączenia do obcego TM.
- [ ] E2E-094 — Zmiana mapy przez jednego klienta: drugi wykrywa nowy epizod,
  nie używa starych ID i wymaga ponownej synchronizacji.

## Kryteria zakończenia

- [ ] Wszystkie przypadki krytyczne lifecycle (`E2E-010`, `013`, `021`–`025`,
  `033`, `050`–`055`, `082`, `091`–`094`) mają wynik PASS albo opisany blocker.
- [ ] Nie wystąpiła natywna asercja `_episode != nullptr` ani podwójne
  sterowanie TM/BasicAgent.
- [ ] Każdy FAIL ma log, mapę, ustawienia, sekwencję kroków i klasyfikację:
  kod aplikacji / ograniczenie CARLA / środowisko.
- [ ] Wyniki zostały zapisane w osobnym raporcie wykonania; ta checklista
  pozostaje katalogiem przypadków, a nie deklaracją zaliczenia.

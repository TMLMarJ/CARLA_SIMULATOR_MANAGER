import unittest

from gui.widgets.dashboard import SimulationDashboardFrame


class FakeTree:
    def __init__(self):
        self.items = {}

    def exists(self, item_id):
        return item_id in self.items

    def insert(self, parent, index, iid, values):
        self.items[iid] = tuple(values)

    def item(self, item_id, values):
        self.items[item_id] = tuple(values)

    def get_children(self, parent):
        return tuple(self.items)

    def delete(self, item_id):
        del self.items[item_id]


class DashboardPresentationTests(unittest.TestCase):
    def test_control_states_are_presented_without_overstating_pause(self):
        format_state = SimulationDashboardFrame.format_simulation_state

        self.assertEqual(
            "Running (GUI Control)",
            format_state("Sync GUI Running"),
        )
        self.assertEqual(
            "GUI Paused (local ticks only)",
            format_state("Sync GUI Paused"),
        )
        self.assertEqual(
            "External Control",
            format_state("Sync External"),
        )
        self.assertEqual(
            "Running (Asynchronous)",
            format_state("Async Running"),
        )

    def test_actor_rows_include_external_actors_and_consistent_totals(self):
        statistics = {
            "actors": [
                {
                    "category": "Vehicles",
                    "ownership": "GUI Managed",
                },
                {
                    "category": "Vehicles",
                    "ownership": "External",
                },
                {
                    "category": "Sensors",
                    "ownership": "Internal",
                },
                {
                    "category": "Other",
                    "ownership": "External",
                },
            ]
        }

        rows = SimulationDashboardFrame.actor_rows(statistics)

        self.assertEqual(2, rows["Vehicles"]["Total"])
        self.assertEqual(1, rows["Vehicles"]["External"])
        self.assertEqual(1, rows["Sensors"]["Internal"])
        self.assertEqual(4, rows["Total"]["Total"])
        self.assertEqual(2, rows["Total"]["External"])

    def test_sensor_table_updates_existing_rows_and_removes_stale_rows(self):
        dashboard = object.__new__(SimulationDashboardFrame)
        dashboard.sensor_tree = FakeTree()
        dashboard.sensor_tree.items["99"] = (99, "old.sensor", "-", "Unknown")

        dashboard._update_sensor_table([
            {
                "actor_id": 5,
                "type_id": "sensor.camera.rgb",
                "parent_id": 2,
                "status": "Streaming",
            },
            {
                "actor_id": 7,
                "type_id": "sensor.other.collision",
                "parent_id": None,
                "status": "Listening",
            },
        ])
        dashboard._update_sensor_table([
            {
                "actor_id": 5,
                "type_id": "sensor.camera.rgb",
                "parent_id": 2,
                "status": "Inactive",
            }
        ])

        self.assertEqual({"5"}, set(dashboard.sensor_tree.items))
        self.assertEqual("Inactive", dashboard.sensor_tree.items["5"][3])

    def test_invalid_timestamps_are_not_exposed_as_current_data(self):
        self.assertEqual("-", SimulationDashboardFrame.format_timestamp(None))
        self.assertEqual("-", SimulationDashboardFrame.format_timestamp("bad"))


if __name__ == "__main__":
    unittest.main()

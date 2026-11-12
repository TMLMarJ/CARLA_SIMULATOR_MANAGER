import unittest
from types import SimpleNamespace
from unittest import mock

from services.world import WorldService
from utilities.map_names import (
    AmbiguousMapError,
    canonical_map_name,
    maps_equal,
    resolve_available_map,
    visible_map_names,
)


class MapNameCompatibilityTests(unittest.TestCase):
    def test_visible_maps_remove_only_empty_map_in_any_position(self):
        maps = [
            "/Game/Carla/Maps/Town01",
            "Carla/Maps/EmptyMap",
            "/Game/Custom/Maps/Town02",
        ]
        self.assertEqual(
            [maps[0], maps[2]],
            visible_map_names(maps),
        )

    def test_visible_maps_keep_similar_names_and_full_paths(self):
        maps = [
            "/Game/Carla/Maps/MyEmptyMap",
            "/Game/Custom/EmptyMapVariant",
            "Town01",
        ]
        self.assertEqual(maps, visible_map_names(maps))

    def test_game_prefix_and_server_relative_path_are_equivalent(self):
        self.assertEqual(
            "Carla/Maps/Town01",
            canonical_map_name("/Game/Carla/Maps/Town01"),
        )
        self.assertTrue(maps_equal(
            "/Game/Carla/Maps/Town01",
            "Carla/Maps/Town01",
        ))

    def test_short_and_full_identifiers_match(self):
        self.assertTrue(maps_equal(
            "Town01",
            "/Game/Carla/Maps/Town01",
        ))

    def test_different_full_paths_do_not_match_by_basename(self):
        self.assertFalse(maps_equal(
            "/Game/Carla/Maps/Town01",
            "/Game/Custom/Maps/Town01",
        ))
        self.assertFalse(maps_equal(
            "/Game/Carla/Maps/Town01",
            "Custom/Maps/Town01",
        ))

    def test_resolver_returns_server_identifier_not_canonical_rewrite(self):
        self.assertEqual(
            "/Game/Carla/Maps/Town01",
            resolve_available_map(
                "Carla/Maps/Town01",
                ["/Game/Carla/Maps/Town01"],
            ),
        )

    def test_short_name_resolves_to_exact_available_server_identifier(self):
        self.assertEqual(
            "/Game/Carla/Maps/Town01",
            resolve_available_map(
                "Town01",
                ["/Game/Carla/Maps/Town01", "/Game/Carla/Maps/Town02"],
            ),
        )

    def test_ambiguous_short_name_is_rejected(self):
        with self.assertRaises(AmbiguousMapError):
            resolve_available_map(
                "Town01",
                [
                    "/Game/Carla/Maps/Town01",
                    "/Game/Custom/Maps/Town01",
                ],
            )

    def test_successful_full_path_load_is_not_reported_as_failure(self):
        world = SimpleNamespace(
            get_map=lambda: SimpleNamespace(
                name="/Game/Carla/Maps/Town01"
            )
        )
        client = SimpleNamespace(
            load_world=mock.Mock(return_value=world),
            get_world=mock.Mock(return_value=world),
        )

        result = WorldService.load_with_retry(
            client,
            "/Game/Carla/Maps/Town01",
            mock.Mock(),
        )

        self.assertIs(world, result)

    def test_world_load_accepts_server_path_without_game_prefix(self):
        active_world = SimpleNamespace(
            id=31,
            get_map=lambda: SimpleNamespace(name="Carla/Maps/Town01"),
        )
        client = SimpleNamespace(
            load_world=mock.Mock(),
            get_world=mock.Mock(return_value=active_world),
        )

        result = WorldService.load_with_retry(
            client,
            "/Game/Carla/Maps/Town01",
            mock.Mock(),
        )

        self.assertIs(active_world, result)
        client.load_world.assert_called_once_with(
            "/Game/Carla/Maps/Town01"
        )

    def test_stale_world_returned_by_load_is_never_validated(self):
        stale_world = mock.Mock()
        stale_world.get_map.side_effect = RuntimeError(
            "world belongs to a different episode"
        )
        active_world = SimpleNamespace(
            id=22,
            get_map=lambda: SimpleNamespace(name="Town02"),
        )
        client = SimpleNamespace(
            load_world=mock.Mock(return_value=stale_world),
            get_world=mock.Mock(return_value=active_world),
        )

        result = WorldService.load_with_retry(
            client,
            "Town02",
            mock.Mock(),
        )

        self.assertIs(active_world, result)
        stale_world.get_map.assert_not_called()

    def test_real_load_error_remains_an_error(self):
        client = SimpleNamespace(
            load_world=mock.Mock(side_effect=RuntimeError("server rejected")),
            get_world=mock.Mock(return_value=SimpleNamespace(
                get_map=lambda: SimpleNamespace(name="Town02")
            )),
        )
        shutdown_event = mock.Mock()
        shutdown_event.wait.return_value = False

        with self.assertRaisesRegex(RuntimeError, "server rejected"):
            WorldService.load_with_retry(
                client,
                "Town01",
                shutdown_event,
                attempts=1,
            )


if __name__ == "__main__":
    unittest.main()

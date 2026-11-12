import unittest

from utilities.functions import weather_parameter_presets


class FakeWeatherParameters:
    pass


FakeWeatherParameters.ClearNoon = FakeWeatherParameters()
FakeWeatherParameters.WetCloudyNoon = FakeWeatherParameters()
FakeWeatherParameters.technical_property = object()
FakeWeatherParameters.helper = lambda: None


class WorldOptionsTests(unittest.TestCase):
    def test_weather_presets_include_every_real_instance_only(self):
        self.assertEqual(
            {"ClearNoon", "WetCloudyNoon"},
            set(weather_parameter_presets(FakeWeatherParameters)),
        )


if __name__ == "__main__":
    unittest.main()

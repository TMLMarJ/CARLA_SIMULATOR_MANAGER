import multiprocessing
import queue
import threading
import time

from preview.spectator_controls import (
    PreviewClosed,
    camera_state_from_transform,
    camera_transform,
    create_overlay,
    current_move_speed,
    process_window_events,
    set_mouse_capture,
    update_camera_state,
    update_window_caption
)


DEFAULT_PREVIEW_CONFIG = {
    "width": 1280,
    "height": 720,
    "fov": 90,
    "sensor_tick": 0.033333,
    "fps": 60,
    "client_timeout": 3.0,
    "world_check_interval": 1.0,
    "move_speed": 10.0,
    "min_move_speed": 0.5,
    "max_move_speed": 100.0,
    "speed_scroll_factor": 1.2,
    "boost_multiplier": 4.0,
    "mouse_sensitivity": 0.12,
    "pitch_limit": 89.0,
    "transform_update_hz": 30.0,
    "max_delta_time": 0.1
}


class SpectatorPreviewManager:
    def __init__(self):
        self.context = multiprocessing.get_context("spawn")
        self.process = None
        self.stop_event = None
        self.event_queue = None
        self.stop_requested_at = None

    def start(self, host, port, config=None):
        self.poll_events()
        if self.process is not None:
            return False

        preview_config = DEFAULT_PREVIEW_CONFIG.copy()
        if config is not None:
            preview_config.update(config)
        preview_config["sensor_tick"] = 1.0 / preview_config["fps"]

        self.stop_event = self.context.Event()
        self.event_queue = self.context.Queue()
        self.process = self.context.Process(
            target=run_spectator_preview,
            args=(
                host,
                port,
                preview_config,
                self.stop_event,
                self.event_queue
            ),
            name="CARLA Spectator Preview"
        )
        self.process.start()
        self.stop_requested_at = None
        return True

    def request_stop(self):
        if self.process is None or self.stop_event is None:
            return False
        if not self.stop_event.is_set():
            self.stop_event.set()
            self.stop_requested_at = time.monotonic()
        return True

    def stop_timed_out(self, timeout):
        return (
            self.process is not None and
            self.process.is_alive() and
            self.stop_requested_at is not None and
            time.monotonic() - self.stop_requested_at >= timeout
        )

    def force_stop(self):
        if self.process is None:
            return True
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=0.2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=0.2)
        if self.process.is_alive():
            return False
        self._release_process_resources()
        return True

    def is_active(self):
        return self.process is not None and self.process.is_alive()

    def poll_events(self):
        events = self._drain_events()
        if self.process is None or self.process.is_alive():
            return events

        self.process.join(timeout=0)
        exit_code = self.process.exitcode
        events.extend(self._drain_events())
        if not any(status == "closed" for status, data in events):
            events.append(("closed", {
                "reason": "process_exited",
                "exit_code": exit_code
            }))
        self._release_process_resources()
        return events

    def _drain_events(self):
        events = []
        if self.event_queue is None:
            return events
        try:
            while True:
                events.append(self.event_queue.get_nowait())
        except queue.Empty:
            pass
        except (EOFError, OSError, ValueError):
            pass
        return events

    def _release_process_resources(self):
        if self.event_queue is not None:
            try:
                self.event_queue.close()
            except (OSError, ValueError):
                pass
        self.process = None
        self.stop_event = None
        self.event_queue = None
        self.stop_requested_at = None


class _LatestRgbFrame:
    def __init__(self):
        self.raw_data = None
        self.width = 0
        self.height = 0
        self.frame = None
        self.revision = 0
        self.closed = False
        self.lock = threading.Lock()

    def on_image(self, image):
        with self.lock:
            if self.closed:
                return
        raw_data = bytes(image.raw_data)
        with self.lock:
            if self.closed:
                return
            self.raw_data = raw_data
            self.width = image.width
            self.height = image.height
            self.frame = image.frame
            self.revision += 1

    def get_snapshot(self):
        with self.lock:
            return (
                self.revision,
                self.raw_data,
                self.width,
                self.height,
                self.frame
            )

    def clear(self):
        with self.lock:
            self.raw_data = None
            self.width = 0
            self.height = 0
            self.frame = None
            self.revision += 1

    def close(self):
        with self.lock:
            self.closed = True
            self.raw_data = None
            self.revision += 1


def _publish_event(event_queue, status, data=None):
    try:
        event_queue.put((status, data))
    except (BrokenPipeError, EOFError, OSError, ValueError):
        pass


def _create_camera(client, frame_buffer, config):
    world = client.get_world()
    spectator = world.get_spectator()
    spectator_transform = spectator.get_transform()
    camera_blueprint = world.get_blueprint_library().find("sensor.camera.rgb")

    attributes = {
        "image_size_x": config["width"],
        "image_size_y": config["height"],
        "fov": config["fov"],
        "sensor_tick": config["sensor_tick"],
        "role_name": "spectator_preview"
    }
    for name, value in attributes.items():
        if camera_blueprint.has_attribute(name):
            camera_blueprint.set_attribute(name, str(value))

    camera = world.spawn_actor(
        camera_blueprint,
        spectator_transform
    )
    if camera is None:
        raise RuntimeError("CARLA did not create the spectator RGB camera.")

    try:
        camera.listen(frame_buffer.on_image)
    except Exception:
        camera.destroy()
        raise

    return world, spectator, camera, spectator_transform


def _destroy_camera(camera):
    if camera is None:
        return
    try:
        if camera.is_listening():
            camera.stop()
    except Exception:
        pass
    try:
        if camera.is_alive:
            camera.destroy()
    except Exception:
        pass


def _create_surface(raw_data, width, height, pygame):
    surface = pygame.image.frombuffer(
        raw_data,
        (width, height),
        "BGRA"
    )
    return surface.convert()


def run_spectator_preview(host, port, config, stop_event, event_queue):
    camera = None
    pygame = None
    frame_buffer = _LatestRgbFrame()
    close_reason = "stop_requested"

    _publish_event(event_queue, "starting")

    try:
        import carla
        import pygame as pygame_module

        if stop_event.is_set():
            raise PreviewClosed("stop_requested")

        pygame = pygame_module
        client = carla.Client(host, port)
        client.set_timeout(config["client_timeout"])

        pygame.init()
        screen = pygame.display.set_mode((config["width"], config["height"]))
        clock = pygame.time.Clock()
        waiting_font = pygame.font.Font(None, 32)
        overlay_font = pygame.font.Font(None, 24)
        help_font = pygame.font.Font(None, 19)
        input_state = {
            "mouse_captured": True,
            "window_focused": True,
            "move_speed": config["move_speed"],
            "current_move_speed": config["move_speed"],
            "show_help": True
        }
        set_mouse_capture(pygame, input_state, True)
        update_window_caption(pygame, input_state["move_speed"])

        screen.fill((24, 24, 24))
        waiting_text = waiting_font.render(
            "Waiting for spectator camera image...",
            True,
            (230, 230, 230)
        )
        text_position = waiting_text.get_rect(
            center=(config["width"] // 2, config["height"] // 2)
        )
        screen.blit(waiting_text, text_position)
        pygame.display.flip()

        if stop_event.is_set():
            raise PreviewClosed("stop_requested")

        world, spectator, camera, spectator_transform = _create_camera(
            client,
            frame_buffer,
            config
        )
        world_id = getattr(world, "id", None)
        camera_state = camera_state_from_transform(
            spectator_transform
        )
        transform_dirty = False
        transform_interval = 1.0 / config["transform_update_hz"]
        next_transform_update = time.monotonic()
        last_revision = -1
        latest_surface = None
        overlay_surface = None
        overlay_dirty = True
        next_overlay_update = time.monotonic()
        next_world_check = time.monotonic() + config["world_check_interval"]

        _publish_event(event_queue, "running", {
            "sensor_id": camera.id,
            "width": config["width"],
            "height": config["height"]
        })

        while not stop_event.is_set():
            delta_time = min(
                clock.tick(config["fps"]) / 1000.0,
                config["max_delta_time"]
            )
            mouse_delta, speed_changed, overlay_changed = process_window_events(
                pygame,
                input_state,
                config
            )
            if speed_changed:
                update_window_caption(pygame, input_state["move_speed"])
            overlay_dirty = overlay_dirty or speed_changed or overlay_changed

            if not pygame.key.get_focused():
                input_state["window_focused"] = False
                if input_state["mouse_captured"]:
                    set_mouse_capture(pygame, input_state, False)

            if input_state["window_focused"]:
                keys = pygame.key.get_pressed()
                move_speed = current_move_speed(
                    keys,
                    input_state["move_speed"],
                    config,
                    pygame
                )
                if move_speed != input_state["current_move_speed"]:
                    input_state["current_move_speed"] = move_speed
                    overlay_dirty = True
                transform_dirty = (
                    update_camera_state(
                        camera_state,
                        keys,
                        mouse_delta,
                        delta_time,
                        move_speed,
                        config,
                        pygame
                    ) or transform_dirty
                )
            elif input_state["current_move_speed"] != input_state["move_speed"]:
                input_state["current_move_speed"] = input_state["move_speed"]
                overlay_dirty = True

            try:
                current_time = time.monotonic()
                if transform_dirty and current_time >= next_transform_update:
                    new_transform = camera_transform(camera_state, carla)
                    spectator.set_transform(new_transform)
                    camera.set_transform(new_transform)
                    transform_dirty = False
                    next_transform_update = current_time + transform_interval

                if current_time >= next_world_check:
                    current_world = client.get_world()
                    current_world_id = getattr(current_world, "id", None)
                    if current_world_id != world_id:
                        close_reason = "world_changed"
                        break
                    if not camera.is_alive:
                        raise RuntimeError(
                            "The Spectator View RGB camera no longer exists."
                        )
                    next_world_check = (
                        time.monotonic() + config["world_check_interval"]
                    )
            except RuntimeError as error:
                raise RuntimeError(
                    f"CARLA communication error: {error}"
                ) from error

            revision, raw_data, width, height, _frame = (
                frame_buffer.get_snapshot()
            )
            redraw = False
            if revision != last_revision and raw_data is not None:
                last_revision = revision
                latest_surface = _create_surface(
                    raw_data,
                    width,
                    height,
                    pygame
                )
                redraw = True

            if overlay_dirty or current_time >= next_overlay_update:
                overlay_surface = create_overlay(
                    pygame,
                    overlay_font,
                    help_font,
                    camera_state,
                    input_state,
                    clock.get_fps()
                )
                overlay_dirty = False
                next_overlay_update = current_time + 0.25
                redraw = True

            if redraw:
                screen.fill((24, 24, 24))
                if latest_surface is None:
                    screen.blit(waiting_text, text_position)
                else:
                    screen.blit(latest_surface, (0, 0))
                if overlay_surface is not None:
                    screen.blit(overlay_surface, (12, 12))
                pygame.display.flip()

        if stop_event.is_set():
            close_reason = "stop_requested"
    except PreviewClosed as closed:
        close_reason = str(closed)
    except Exception as error:
        close_reason = "error"
        _publish_event(event_queue, "error", str(error))
    finally:
        frame_buffer.close()
        _destroy_camera(camera)
        if pygame is not None:
            try:
                pygame.quit()
            except Exception:
                pass
        _publish_event(event_queue, "closed", {
            "reason": close_reason
        })

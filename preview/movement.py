import multiprocessing
import queue
import threading
import time


DEFAULT_MOVEMENT_PREVIEW_CONFIG = {
    "width": 800,
    "height": 450,
    "fov": 90.0,
    "fps": 30,
    "control_hz": 20,
}

CAMERA_PRESETS = ("First Person", "Third Person", "Top View")
MIN_MOVEMENT_PREVIEW_SIZE = (480, 270)
MOVEMENT_HUD_SIZE = (310, 205)
MOVEMENT_HUD_MARGIN = 12


def clamp_preview_window_size(size, minimum=MIN_MOVEMENT_PREVIEW_SIZE):
    try:
        width, height = int(size[0]), int(size[1])
    except (IndexError, TypeError, ValueError, OverflowError):
        return tuple(minimum)
    return max(int(minimum[0]), width), max(int(minimum[1]), height)


def contained_image_geometry(source_size, target_size):
    """Return an integer fit/contain rectangle without cropping."""
    try:
        source_width, source_height = (
            int(source_size[0]), int(source_size[1])
        )
        target_width, target_height = (
            int(target_size[0]), int(target_size[1])
        )
    except (IndexError, TypeError, ValueError, OverflowError):
        return 0, 0, 0, 0
    if min(source_width, source_height, target_width, target_height) <= 0:
        return 0, 0, 0, 0

    scale = min(
        target_width / source_width,
        target_height / source_height,
    )
    width = max(1, min(target_width, int(source_width * scale)))
    height = max(1, min(target_height, int(source_height * scale)))
    return (
        (target_width - width) // 2,
        (target_height - height) // 2,
        width,
        height,
    )


def movement_hud_geometry(window_size, panel_size=MOVEMENT_HUD_SIZE,
                          margin=MOVEMENT_HUD_MARGIN):
    try:
        window_width, window_height = (
            max(1, int(window_size[0])),
            max(1, int(window_size[1])),
        )
    except (IndexError, TypeError, ValueError, OverflowError):
        window_width, window_height = MIN_MOVEMENT_PREVIEW_SIZE
    margin = max(0, int(margin))
    width = min(int(panel_size[0]), max(1, window_width - 2 * margin))
    height = min(int(panel_size[1]), max(1, window_height - 2 * margin))
    return margin, margin, width, height


def resize_movement_display(pygame, requested_size):
    size = clamp_preview_window_size(requested_size)
    return pygame.display.set_mode(size, pygame.RESIZABLE), size


def normalize_vehicle_telemetry(control_state):
    """Return bounded, display-ready telemetry from a CARLA control state."""
    control_state = control_state if isinstance(control_state, dict) else {}

    def number(name, default=0.0, minimum=None, maximum=None):
        try:
            value = float(control_state.get(name, default))
        except (TypeError, ValueError, OverflowError):
            value = float(default)
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    try:
        gear = int(control_state.get("gear", 0))
    except (TypeError, ValueError, OverflowError):
        gear = 0
    return {
        "mode": str(control_state.get("mode") or "No Control"),
        "speed_kmh": number("current_speed", minimum=0.0) * 3.6,
        "gear": gear,
        "throttle": number("throttle", minimum=0.0, maximum=1.0),
        "brake": number("brake", minimum=0.0, maximum=1.0),
        "steer": number("steer", minimum=-1.0, maximum=1.0),
        "hand_brake": bool(control_state.get("hand_brake", False)),
        "reverse": bool(control_state.get("reverse", False)),
    }


def toggle_hud_visibility(visible, key_name):
    return not visible if str(key_name).casefold() == "h" else visible


def draw_movement_hud(pygame, screen, telemetry, fonts):
    if not telemetry:
        return
    panel_x, panel_y, width, height = movement_hud_geometry(
        screen.get_size()
    )
    panel = pygame.Surface((width, height), pygame.SRCALPHA)
    panel.fill((20, 20, 20, 178))
    title_font, speed_font, text_font = fonts
    color = (238, 238, 238)
    muted = (190, 190, 190)
    accent = (226, 132, 72)
    panel.blit(title_font.render(telemetry["mode"], True, color), (14, 10))
    panel.blit(
        speed_font.render(f'{telemetry["speed_kmh"]:.1f} km/h', True, color),
        (14, 38),
    )
    gear = "R" if telemetry["reverse"] else str(telemetry["gear"])
    panel.blit(
        text_font.render(f"Gear: {gear}", True, muted),
        (width - 100, 54),
    )

    bar_left = 92
    bar_right = width - 28
    bar_width = max(1, bar_right - bar_left)

    def draw_bar(label, value, y, fill):
        panel.blit(text_font.render(label, True, muted), (14, y))
        pygame.draw.rect(
            panel,
            (75, 75, 75),
            (bar_left, y + 3, bar_width, 14),
        )
        pygame.draw.rect(
            panel,
            fill,
            (bar_left, y + 3, int(bar_width * value), 14),
        )

    draw_bar("Throttle", telemetry["throttle"], 100, accent)
    draw_bar("Brake", telemetry["brake"], 126, (205, 75, 65))
    panel.blit(text_font.render("Steering", True, muted), (14, 152))
    steering_center = (bar_left + bar_right) // 2
    steering_radius = max(1, (bar_right - bar_left) // 2)
    pygame.draw.line(
        panel,
        (95, 95, 95),
        (bar_left, 162),
        (bar_right, 162),
        4,
    )
    pygame.draw.line(
        panel,
        color,
        (steering_center, 154),
        (steering_center, 170),
        2,
    )
    steer_x = steering_center + int(steering_radius * telemetry["steer"])
    pygame.draw.circle(panel, accent, (steer_x, 162), 6)
    flags = []
    if telemetry["hand_brake"]:
        flags.append("Handbrake")
    if telemetry["reverse"]:
        flags.append("Reverse")
    panel.blit(text_font.render(" | ".join(flags) or "H: hide HUD", True, muted), (14, 180))
    screen.blit(panel, (panel_x, panel_y))


def _approach(current, target, rate, delta_time):
    change = rate * max(0.0, delta_time)
    if current < target:
        return min(target, current + change)
    return max(target, current - change)


class ManualControlSmoother:
    def __init__(self):
        self.throttle = 0.0
        self.brake = 0.0
        self.steer = 0.0
        self.reverse = False
        self.reverse_key_down = False

    def update(self, keys, delta_time, focused=True):
        reverse_pressed = bool(keys.get("reverse", False)) if focused else False
        if reverse_pressed and not self.reverse_key_down:
            self.reverse = not self.reverse
        self.reverse_key_down = reverse_pressed

        if not focused:
            self.throttle = 0.0
            self.brake = 1.0
            self.steer = 0.0
            return self.snapshot(focused=False, hand_brake=False)

        accelerating = bool(keys.get("accelerate", False))
        braking = bool(keys.get("brake", False))
        target_throttle = 1.0 if accelerating and not braking else 0.0
        target_brake = 1.0 if braking else 0.0
        steer_input = float(bool(keys.get("right", False))) - float(
            bool(keys.get("left", False))
        )

        throttle_rate = 1.6 if target_throttle > self.throttle else 3.5
        brake_rate = 2.8 if target_brake > self.brake else 4.5
        steer_rate = 2.5 if steer_input != 0.0 else 4.0
        self.throttle = _approach(
            self.throttle, target_throttle, throttle_rate, delta_time
        )
        self.brake = _approach(
            self.brake, target_brake, brake_rate, delta_time
        )
        self.steer = _approach(
            self.steer, steer_input, steer_rate, delta_time
        )
        return self.snapshot(
            focused=True,
            hand_brake=bool(keys.get("hand_brake", False)),
        )

    def snapshot(self, focused, hand_brake):
        return {
            "throttle": self.throttle,
            "brake": self.brake,
            "steer": self.steer,
            "hand_brake": bool(hand_brake),
            "reverse": self.reverse,
            "focused": bool(focused),
        }


def camera_transform_for_actor(actor_type, preset, carla_module):
    if preset not in CAMERA_PRESETS:
        raise ValueError(f"Unknown camera preset: {preset}")

    is_walker = str(actor_type).startswith("walker.")
    if preset == "First Person":
        location = (0.15, 0.0, 1.65) if is_walker else (1.25, 0.0, 1.35)
        rotation = (0.0, 0.0, 0.0)
    elif preset == "Third Person":
        location = (-3.5, 0.0, 2.2) if is_walker else (-6.0, 0.0, 3.0)
        rotation = (-10.0, 0.0, 0.0) if is_walker else (-12.0, 0.0, 0.0)
    else:
        location = (0.0, 0.0, 7.0) if is_walker else (0.0, 0.0, 12.0)
        rotation = (-90.0, 0.0, 0.0)

    return carla_module.Transform(
        carla_module.Location(x=location[0], y=location[1], z=location[2]),
        carla_module.Rotation(
            pitch=rotation[0],
            yaw=rotation[1],
            roll=rotation[2],
        ),
    )


class LatestFramePublisher:
    """Copies a transient CARLA image into a bounded latest-frame queue."""

    def __init__(self, frame_queue):
        self.frame_queue = frame_queue
        self.closed = False
        self.lock = threading.Lock()

    def publish(self, image):
        with self.lock:
            if self.closed:
                return
            packet = (
                bytes(image.raw_data),
                int(image.width),
                int(image.height),
                int(image.frame),
            )
            try:
                self.frame_queue.put_nowait(packet)
                return
            except queue.Full:
                pass
            except (BrokenPipeError, EOFError, OSError, ValueError):
                self.closed = True
                return

            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                return
            except (EOFError, OSError, ValueError):
                self.closed = True
                return

            try:
                self.frame_queue.put_nowait(packet)
            except queue.Full:
                pass
            except (BrokenPipeError, EOFError, OSError, ValueError):
                self.closed = True

    def close(self):
        with self.lock:
            self.closed = True


def _publish_event(event_queue, status, data=None):
    try:
        event_queue.put_nowait((status, data))
    except queue.Full:
        pass


def _put_latest(ipc_queue, value):
    try:
        ipc_queue.put_nowait(value)
        return
    except queue.Full:
        pass
    except (BrokenPipeError, EOFError, OSError, ValueError):
        return
    try:
        ipc_queue.get_nowait()
    except queue.Empty:
        return
    except (EOFError, OSError, ValueError):
        return
    try:
        ipc_queue.put_nowait(value)
    except (queue.Full, BrokenPipeError, EOFError, OSError, ValueError):
        pass


def run_movement_preview(frame_queue, control_queue, telemetry_queue,
                         stop_event, event_queue, config):
    pygame = None
    close_reason = "stop_requested"
    _publish_event(event_queue, "starting")

    try:
        import pygame as pygame_module

        pygame = pygame_module
        pygame.init()
        screen, window_size = resize_movement_display(
            pygame,
            (config["width"], config["height"]),
        )
        pygame.display.set_caption("CARLA Movement Preview")
        clock = pygame.time.Clock()
        smoother = ManualControlSmoother()
        window_focused = True
        control_interval = 1.0 / config["control_hz"]
        next_control_update = time.monotonic()
        font = pygame.font.Font(None, 30)
        hud_fonts = (
            pygame.font.Font(None, 25),
            pygame.font.Font(None, 38),
            pygame.font.Font(None, 21),
        )
        hud_visible = True
        telemetry = None
        camera_surface = None
        scaled_camera_surface = None
        scaled_camera_position = (0, 0)
        scaled_camera_key = None
        waiting = font.render(
            "Waiting for the attached RGB camera...",
            True,
            (230, 230, 230),
        )
        waiting_position = waiting.get_rect(
            center=(window_size[0] // 2, window_size[1] // 2)
        )
        screen.fill((24, 24, 24))
        screen.blit(waiting, waiting_position)
        pygame.display.flip()
        _publish_event(event_queue, "running", {
            "width": config["width"],
            "height": config["height"],
        })

        while not stop_event.is_set():
            delta_time = min(clock.tick(config["fps"]) / 1000.0, 0.1)
            requested_resize = None
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    close_reason = "window_closed"
                    _put_latest(
                        control_queue,
                        smoother.update({}, delta_time, focused=False),
                    )
                    stop_event.set()
                    break
                if event.type == pygame.KEYDOWN and event.key == pygame.K_h:
                    hud_visible = toggle_hud_visibility(hud_visible, "h")
                if event.type == pygame.VIDEORESIZE:
                    requested_resize = (
                        getattr(event, "w", window_size[0]),
                        getattr(event, "h", window_size[1]),
                    )
                if event.type == getattr(pygame, "WINDOWFOCUSLOST", None):
                    window_focused = False
                elif event.type == getattr(pygame, "WINDOWFOCUSGAINED", None):
                    window_focused = True

            if requested_resize is not None:
                screen, window_size = resize_movement_display(
                    pygame,
                    requested_resize,
                )
                waiting_position = waiting.get_rect(
                    center=(window_size[0] // 2, window_size[1] // 2)
                )
                scaled_camera_key = None

            window_focused = window_focused and pygame.key.get_focused()
            pressed = pygame.key.get_pressed()
            key_state = {
                "accelerate": pressed[pygame.K_w],
                "brake": pressed[pygame.K_s],
                "left": pressed[pygame.K_a],
                "right": pressed[pygame.K_d],
                "hand_brake": pressed[pygame.K_SPACE],
                "reverse": pressed[pygame.K_r],
            }
            control = smoother.update(
                key_state,
                delta_time,
                focused=window_focused,
            )
            now = time.monotonic()
            if now >= next_control_update:
                _put_latest(control_queue, control)
                next_control_update = now + control_interval

            latest_packet = None
            try:
                while True:
                    latest_packet = frame_queue.get_nowait()
            except queue.Empty:
                pass
            except (EOFError, OSError, ValueError):
                close_reason = "frame_channel_closed"
                break

            if latest_packet is not None:
                raw_data, width, height, _frame = latest_packet
                camera_surface = pygame.image.frombuffer(
                    raw_data,
                    (width, height),
                    "BGRA",
                )
                scaled_camera_key = None
            latest_telemetry = None
            try:
                while True:
                    latest_telemetry = telemetry_queue.get_nowait()
            except queue.Empty:
                pass
            except (EOFError, OSError, ValueError):
                close_reason = "telemetry_channel_closed"
                break
            if latest_telemetry is not None:
                telemetry = normalize_vehicle_telemetry(latest_telemetry)

            screen.fill((24, 24, 24))
            if camera_surface is not None:
                camera_key = (camera_surface.get_size(), window_size)
                if camera_key != scaled_camera_key:
                    x, y, width, height = contained_image_geometry(
                        camera_surface.get_size(),
                        window_size,
                    )
                    target_size = (width, height)
                    scaled_camera_surface = (
                        camera_surface
                        if target_size == camera_surface.get_size()
                        else pygame.transform.smoothscale(
                            camera_surface,
                            target_size,
                        )
                    )
                    scaled_camera_position = (x, y)
                    scaled_camera_key = camera_key
                screen.blit(
                    scaled_camera_surface,
                    scaled_camera_position,
                )
            else:
                screen.blit(waiting, waiting_position)
            if hud_visible:
                draw_movement_hud(pygame, screen, telemetry, hud_fonts)
            pygame.display.flip()

    except Exception as error:
        close_reason = "error"
        _publish_event(event_queue, "error", str(error))
    finally:
        if pygame is not None:
            try:
                pygame.quit()
            except Exception:
                pass
        _publish_event(event_queue, "closed", {"reason": close_reason})


class MovementPreviewProcess:
    def __init__(self):
        self.context = multiprocessing.get_context("spawn")
        self.process = None
        self.stop_event = None
        self.frame_queue = None
        self.control_queue = None
        self.telemetry_queue = None
        self.event_queue = None
        self.stop_requested_at = None

    def start(self, config=None):
        self.poll_events()
        if self.process is not None:
            return False

        preview_config = DEFAULT_MOVEMENT_PREVIEW_CONFIG.copy()
        if config is not None:
            preview_config.update(config)

        self.stop_event = self.context.Event()
        self.frame_queue = self.context.Queue(maxsize=1)
        self.control_queue = self.context.Queue(maxsize=1)
        self.telemetry_queue = self.context.Queue(maxsize=1)
        self.event_queue = self.context.Queue(maxsize=16)
        self.process = self.context.Process(
            target=run_movement_preview,
            args=(
                self.frame_queue,
                self.control_queue,
                self.telemetry_queue,
                self.stop_event,
                self.event_queue,
                preview_config,
            ),
            name="CARLA Movement Preview",
        )
        try:
            self.process.start()
        except Exception:
            self._release_resources()
            return False
        self.stop_requested_at = None
        return True

    def publish_telemetry(self, control_state):
        if self.process is None or self.telemetry_queue is None:
            return False
        _put_latest(self.telemetry_queue, dict(control_state or {}))
        return True

    def request_stop(self):
        if self.process is None or self.stop_event is None:
            return False
        if not self.stop_event.is_set():
            self.stop_event.set()
            self.stop_requested_at = time.monotonic()
        return True

    def is_active(self):
        return self.process is not None and self.process.is_alive()

    def stop_timed_out(self, timeout):
        return (
            self.is_active()
            and self.stop_requested_at is not None
            and time.monotonic() - self.stop_requested_at >= timeout
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
        self._release_resources()
        return True

    def poll_events(self):
        events = self._drain_events()
        if self.process is None or self.process.is_alive():
            return events

        self.process.join(timeout=0)
        exit_code = self.process.exitcode
        events.extend(self._drain_events())
        if not any(status == "closed" for status, _data in events):
            events.append(("closed", {
                "reason": "process_exited",
                "exit_code": exit_code,
            }))
        self._release_resources()
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

    def _release_resources(self):
        for ipc_queue in (
            self.frame_queue,
            self.control_queue,
            self.telemetry_queue,
            self.event_queue,
        ):
            if ipc_queue is None:
                continue
            try:
                ipc_queue.cancel_join_thread()
                ipc_queue.close()
            except (OSError, ValueError):
                pass
        process = self.process
        if process is not None:
            close = getattr(process, "close", None)
            if close is not None:
                try:
                    close()
                except (OSError, ValueError):
                    pass
        self.process = None
        self.stop_event = None
        self.frame_queue = None
        self.control_queue = None
        self.telemetry_queue = None
        self.event_queue = None
        self.stop_requested_at = None

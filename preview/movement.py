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


def run_movement_preview(frame_queue, control_queue, stop_event,
                         event_queue, config):
    pygame = None
    close_reason = "stop_requested"
    _publish_event(event_queue, "starting")

    try:
        import pygame as pygame_module

        pygame = pygame_module
        pygame.init()
        screen = pygame.display.set_mode((config["width"], config["height"]))
        pygame.display.set_caption("CARLA Movement Preview")
        clock = pygame.time.Clock()
        smoother = ManualControlSmoother()
        window_focused = True
        control_interval = 1.0 / config["control_hz"]
        next_control_update = time.monotonic()
        font = pygame.font.Font(None, 30)
        waiting = font.render(
            "Waiting for the attached RGB camera...",
            True,
            (230, 230, 230),
        )
        waiting_position = waiting.get_rect(
            center=(config["width"] // 2, config["height"] // 2)
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
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    close_reason = "window_closed"
                    _put_latest(
                        control_queue,
                        smoother.update({}, delta_time, focused=False),
                    )
                    stop_event.set()
                    break
                if event.type == getattr(pygame, "WINDOWFOCUSLOST", None):
                    window_focused = False
                elif event.type == getattr(pygame, "WINDOWFOCUSGAINED", None):
                    window_focused = True

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
                surface = pygame.image.frombuffer(
                    raw_data,
                    (width, height),
                    "BGRA",
                )
                if (width, height) != (config["width"], config["height"]):
                    surface = pygame.transform.smoothscale(
                        surface,
                        (config["width"], config["height"]),
                    )
                screen.blit(surface, (0, 0))
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
        self.event_queue = self.context.Queue(maxsize=16)
        self.process = self.context.Process(
            target=run_movement_preview,
            args=(
                self.frame_queue,
                self.control_queue,
                self.stop_event,
                self.event_queue,
                preview_config,
            ),
            name="CARLA Movement Preview",
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
            self.event_queue,
        ):
            if ipc_queue is None:
                continue
            try:
                ipc_queue.cancel_join_thread()
                ipc_queue.close()
            except (OSError, ValueError):
                pass
        self.process = None
        self.stop_event = None
        self.frame_queue = None
        self.control_queue = None
        self.event_queue = None
        self.stop_requested_at = None

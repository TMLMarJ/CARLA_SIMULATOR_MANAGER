import math


class PreviewClosed(Exception):
    pass


def camera_state_from_transform(transform):
    return {
        "x": transform.location.x,
        "y": transform.location.y,
        "z": transform.location.z,
        "pitch": transform.rotation.pitch,
        "yaw": transform.rotation.yaw,
        "roll": transform.rotation.roll
    }


def camera_transform(camera_state, carla):
    return carla.Transform(
        carla.Location(
            x=camera_state["x"],
            y=camera_state["y"],
            z=camera_state["z"]
        ),
        carla.Rotation(
            pitch=camera_state["pitch"],
            yaw=camera_state["yaw"],
            roll=camera_state["roll"]
        )
    )


def movement_direction(camera_state, forward_input,
                       right_input, vertical_input):
    yaw = math.radians(camera_state["yaw"])
    pitch = math.radians(camera_state["pitch"])

    forward = (
        math.cos(pitch) * math.cos(yaw),
        math.cos(pitch) * math.sin(yaw),
        math.sin(pitch)
    )
    right = (-math.sin(yaw), math.cos(yaw), 0.0)

    direction = [
        forward[0] * forward_input + right[0] * right_input,
        forward[1] * forward_input + right[1] * right_input,
        forward[2] * forward_input + vertical_input
    ]
    length = math.sqrt(sum(value * value for value in direction))
    if length > 0.0:
        direction = [value / length for value in direction]
    return direction


def current_move_speed(keys, move_speed, config, pygame):
    if keys[pygame.K_LSHIFT]:
        return move_speed * config["boost_multiplier"]
    return move_speed


def update_camera_state(camera_state, keys, mouse_delta,
                        delta_time, move_speed, config, pygame):
    changed = False
    mouse_x, mouse_y = mouse_delta
    if mouse_x != 0 or mouse_y != 0:
        camera_state["yaw"] = (
            camera_state["yaw"] + mouse_x * config["mouse_sensitivity"]
        ) % 360.0
        pitch = (
            camera_state["pitch"] -
            mouse_y * config["mouse_sensitivity"]
        )
        camera_state["pitch"] = max(
            -config["pitch_limit"],
            min(config["pitch_limit"], pitch)
        )
        changed = True

    forward_input = float(keys[pygame.K_w]) - float(keys[pygame.K_s])
    right_input = float(keys[pygame.K_d]) - float(keys[pygame.K_a])
    vertical_input = float(keys[pygame.K_e]) - float(keys[pygame.K_q])

    if forward_input != 0.0 or right_input != 0.0 or vertical_input != 0.0:
        direction = movement_direction(
            camera_state,
            forward_input,
            right_input,
            vertical_input
        )
        distance = move_speed * delta_time
        camera_state["x"] += direction[0] * distance
        camera_state["y"] += direction[1] * distance
        camera_state["z"] += direction[2] * distance
        changed = True

    return changed


def set_mouse_capture(pygame, input_state, captured):
    input_state["mouse_captured"] = captured
    pygame.event.set_grab(captured)
    pygame.mouse.set_visible(not captured)
    pygame.mouse.get_rel()


def process_window_events(pygame, input_state, config):
    mouse_delta = [0, 0]
    speed_changed = False
    overlay_changed = False
    focus_lost_event = getattr(pygame, "WINDOWFOCUSLOST", None)
    focus_gained_event = getattr(pygame, "WINDOWFOCUSGAINED", None)

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            raise PreviewClosed("window_closed")
        if event.type == focus_lost_event:
            input_state["window_focused"] = False
            set_mouse_capture(pygame, input_state, False)
            mouse_delta = [0, 0]
        elif event.type == focus_gained_event:
            input_state["window_focused"] = True
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            set_mouse_capture(pygame, input_state, False)
            mouse_delta = [0, 0]
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_h:
            input_state["show_help"] = not input_state["show_help"]
            overlay_changed = True
        elif (
            event.type == pygame.MOUSEBUTTONDOWN and
            event.button == 1
        ):
            input_state["window_focused"] = True
            set_mouse_capture(pygame, input_state, True)
        elif event.type == pygame.MOUSEWHEEL:
            speed = (
                input_state["move_speed"] *
                config["speed_scroll_factor"] ** event.y
            )
            input_state["move_speed"] = max(
                config["min_move_speed"],
                min(config["max_move_speed"], speed)
            )
            speed_changed = True
        elif (
            event.type == pygame.MOUSEMOTION and
            input_state["mouse_captured"] and
            input_state["window_focused"]
        ):
            mouse_delta[0] += event.rel[0]
            mouse_delta[1] += event.rel[1]

    return tuple(mouse_delta), speed_changed, overlay_changed


def update_window_caption(pygame, move_speed):
    pygame.display.set_caption(
        f"CARLA Spectator View | Speed: {move_speed:.1f} m/s"
    )


def create_overlay(pygame, font, help_font, camera_state,
                   input_state, current_fps):
    lines = [
        (font, f"FPS: {current_fps:.1f}"),
        (font, f"Speed: {input_state['current_move_speed']:.1f} m/s"),
        (
            font,
            f"Position: X {camera_state['x']:.1f} | "
            f"Y {camera_state['y']:.1f} | Z {camera_state['z']:.1f}"
        )
    ]

    if input_state["show_help"]:
        lines.extend((
            (help_font, "WASD - move | Q/E - vertical | Shift - boost"),
            (help_font, "Mouse - look | Scroll - speed | Esc - release"),
            (help_font, "Click - capture mouse | H - hide controls")
        ))
    else:
        lines.append((help_font, "H - show controls"))

    rendered_lines = [line_font.render(text, True, (240, 240, 240))
                      for line_font, text in lines]
    padding = 10
    spacing = 4
    width = max(line.get_width() for line in rendered_lines) + padding * 2
    height = (
        sum(line.get_height() for line in rendered_lines) +
        spacing * (len(rendered_lines) - 1) +
        padding * 2
    )
    overlay = pygame.Surface((width, height), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 155))

    y = padding
    for line in rendered_lines:
        overlay.blit(line, (padding, y))
        y += line.get_height() + spacing
    return overlay

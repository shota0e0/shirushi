"""Cancelable and restartable scheduling for Personal Mark replay."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from personal_mark import PersonalMark, PersonalMarkRenderer, RenderPoint


class ReplayScheduler(Protocol):
    def after(self, delay_ms: int, callback: Callable[[], None]) -> Any: ...

    def after_cancel(self, identifier: Any) -> None: ...


class PersonalMarkReplayController:
    """Schedule renderer points while making cancel and restart deterministic."""

    def __init__(
        self,
        scheduler: ReplayScheduler,
        draw_point: Callable[[RenderPoint], None],
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.draw_point = draw_point
        self.on_complete = on_complete
        self._scheduled: list[Any] = []
        self._generation = 0
        self.running = False

    def start(self, mark: PersonalMark, width: int, height: int) -> None:
        self.cancel()
        self._generation += 1
        generation = self._generation
        points = PersonalMarkRenderer.replay_points(mark, width, height)
        if not points:
            self._finish(generation)
            return
        self.running = True
        for point in points:
            self._scheduled.append(
                self.scheduler.after(
                    point.delay_ms,
                    lambda current=point, token=generation: self._draw(current, token),
                )
            )
        self._scheduled.append(
            self.scheduler.after(
                points[-1].delay_ms + 1,
                lambda token=generation: self._finish(token),
            )
        )

    def cancel(self) -> None:
        self._generation += 1
        for identifier in self._scheduled:
            try:
                self.scheduler.after_cancel(identifier)
            except Exception:
                # Tk can reject an identifier whose callback has just run.
                pass
        self._scheduled.clear()
        self.running = False

    def _draw(self, point: RenderPoint, generation: int) -> None:
        if self.running and generation == self._generation:
            self.draw_point(point)

    def _finish(self, generation: int) -> None:
        if generation != self._generation:
            return
        self._scheduled.clear()
        self.running = False
        if self.on_complete is not None:
            self.on_complete()

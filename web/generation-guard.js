export class GenerationGuard {
  #generation = 0;

  next() {
    this.#generation += 1;
    return this.#generation;
  }

  current() {
    return this.#generation;
  }

  accepts(generation) {
    return generation === this.#generation;
  }

  invalidate() {
    return this.next();
  }
}

export class MotionScheduler {
  #guard = new GenerationGuard();
  #timers = new Set();
  #setTimer;
  #clearTimer;

  constructor(setTimer = setTimeout, clearTimer = clearTimeout) {
    this.#setTimer = setTimer;
    this.#clearTimer = clearTimer;
  }

  begin() {
    this.cancel();
    return this.#guard.current();
  }

  schedule(generation, callback, delay) {
    const timer = this.#setTimer(() => {
      this.#timers.delete(timer);
      if (this.#guard.accepts(generation)) callback();
    }, delay);
    this.#timers.add(timer);
    return timer;
  }

  accepts(generation) {
    return this.#guard.accepts(generation);
  }

  cancel() {
    for (const timer of this.#timers) this.#clearTimer(timer);
    this.#timers.clear();
    this.#guard.invalidate();
  }
}

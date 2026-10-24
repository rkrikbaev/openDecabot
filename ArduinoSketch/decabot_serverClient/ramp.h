/*
 * Smooth acceleration for one motor.
 *
 * The commanded power is the target; the power actually written to the
 * servo climbs towards it on a straight line that goes from 0 to the
 * target in rampMs milliseconds. Slowing down, stopping and the failsafe
 * are immediate. Reversing stops at once and then accelerates the other way.
 * rampMs == 0 disables the ramp.
 *
 * Plain C++ with no Arduino dependency so it can be unit-tested on a
 * computer:  g++ -x c++ -DRAMP_SELFTEST ramp.h -o ramp_test && ./ramp_test
 */
#pragma once

struct Ramp {
  int target = 0;      // power requested by the last command, -100..100
  float current = 0;   // power applied right now

  static int clamp(int p) { return p > 100 ? 100 : (p < -100 ? -100 : p); }
  static float fabsf_(float v) { return v < 0 ? -v : v; }

  // New command. Returns true when the applied power changed immediately.
  bool set(int power, unsigned long rampMs) {
    target = clamp(power);
    float before = current;
    bool sameDirection = (target >= 0) == (current >= 0);
    if (rampMs == 0 || target == 0) {
      current = target;                 // ramp off, or stop: immediate
    } else if (!sameDirection) {
      current = 0;                      // reversing: stop now, ramp from zero
    } else if (fabsf_(target) < fabsf_(current)) {
      current = target;                 // slowing down: immediate
    }
    return current != before;
  }

  // Called periodically with the elapsed time. Returns true when changed.
  bool step(unsigned long dtMs, unsigned long rampMs) {
    if (current == target) return false;
    if (rampMs == 0) { current = target; return true; }
    float stepSize = fabsf_(target) * (float)dtMs / (float)rampMs;
    if (current < target) {
      current += stepSize;
      if (current > target) current = target;
    } else {
      current -= stepSize;
      if (current < target) current = target;
    }
    if (fabsf_(current - target) < 0.01f) current = target;   // float drift
    return true;
  }

  int applied() const { return (int)(current + (current >= 0 ? 0.5f : -0.5f)); }
  bool moving() const { return current != 0 || target != 0; }
};

#ifdef RAMP_SELFTEST
#include <cstdio>
#include <cstdlib>
static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { std::printf("FAIL line %d: %s\n", __LINE__, #cond); failures++; } } while (0)

int main() {
  Ramp r;
  // 0 -> 60 in 2000 ms: half way after 1000 ms
  CHECK(!r.set(60, 2000));
  CHECK(r.applied() == 0);
  for (int t = 0; t < 1000; t += 20) r.step(20, 2000);
  CHECK(r.applied() == 30);
  for (int t = 0; t < 1000; t += 20) r.step(20, 2000);
  CHECK(r.applied() == 60);
  CHECK(!r.step(20, 2000));          // settled
  // slowing down is immediate
  CHECK(r.set(30, 2000));
  CHECK(r.applied() == 30);
  // speeding up again ramps from the current value, not from zero
  r.set(100, 2000);
  CHECK(r.applied() == 30);
  r.step(700, 2000);                 // +35 in 700 ms at 100 % target
  CHECK(r.applied() == 65);
  // reversing: stop now, then ramp the other way
  r.set(-80, 2000);
  CHECK(r.applied() == 0);
  r.step(500, 2000);
  CHECK(r.applied() == -20);
  // stop is immediate
  r.set(0, 2000);
  CHECK(r.applied() == 0 && !r.moving());
  // ramp disabled
  r.set(90, 0);
  CHECK(r.applied() == 90);
  // clamp
  r.set(250, 0);
  CHECK(r.applied() == 100);
  // ramp time changed at runtime applies to the remaining distance
  Ramp q; q.set(100, 1000); q.step(500, 1000); CHECK(q.applied() == 50);
  q.step(250, 500); CHECK(q.applied() == 100);
  if (failures == 0) std::printf("ramp.h self-test OK\n");
  return failures ? 1 : 0;
}
#endif

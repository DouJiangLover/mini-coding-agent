import assert from 'node:assert/strict';
import test from 'node:test';

import { GAME_DURATION, createGameState, registerHit, registerMiss, startGame, tick } from '../src/game.js';

test('new game starts with a clean scoreboard', () => {
  const state = startGame();

  assert.equal(state.score, 0);
  assert.equal(state.combo, 0);
  assert.equal(state.remaining, GAME_DURATION);
  assert.equal(state.status, 'running');
});

test('consecutive hits build combo and bonus score', () => {
  const firstHit = registerHit(startGame());
  const secondHit = registerHit(firstHit);

  assert.equal(secondHit.combo, 2);
  assert.equal(secondHit.bestCombo, 2);
  assert.equal(secondHit.score, 26);
});

test('missing the target resets only the current combo', () => {
  const state = registerMiss({
    ...createGameState(),
    status: 'running',
    score: 38,
    combo: 3,
    bestCombo: 5,
  });

  assert.equal(state.score, 38);
  assert.equal(state.combo, 0);
  assert.equal(state.bestCombo, 5);
});

test('timer stops the game at zero', () => {
  const state = tick({ ...startGame(), remaining: 1 });

  assert.equal(state.remaining, 0);
  assert.equal(state.status, 'finished');
});

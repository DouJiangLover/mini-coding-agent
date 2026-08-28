export const GAME_DURATION = 20;

export function createGameState() {
  return {
    score: 0,
    combo: 0,
    bestCombo: 0,
    remaining: GAME_DURATION,
    status: 'idle',
  };
}

export function startGame() {
  return { ...createGameState(), status: 'running' };
}

export function registerHit(state) {
  if (state.status !== 'running') return state;
  const nextCombo = state.combo + 1;
  const gainedScore = 10 + nextCombo * 2;
  return {
    ...state,
    score: state.score + gainedScore,
    combo: nextCombo,
    bestCombo: Math.max(state.bestCombo, nextCombo),
  };
}

export function registerMiss(state) {
  if (state.status !== 'running') return state;
  return { ...state, combo: 0 };
}

export function tick(state) {
  if (state.status !== 'running') return state;
  const remaining = Math.max(0, state.remaining - 1);
  return {
    ...state,
    remaining,
    status: remaining === 0 ? 'finished' : 'running',
  };
}

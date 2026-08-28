import { createGameState, registerHit, registerMiss, startGame, tick } from './game.js';

const arena = document.querySelector('#arena');
const target = document.querySelector('#target');
const startButton = document.querySelector('#start-button');
const emptyState = document.querySelector('#empty-state');
const score = document.querySelector('#score');
const combo = document.querySelector('#combo');
const bestCombo = document.querySelector('#best-combo');
const time = document.querySelector('#time');
const status = document.querySelector('#status');

let state = createGameState();
let timer;

function render() {
  score.textContent = String(state.score);
  combo.textContent = String(state.combo);
  bestCombo.textContent = String(state.bestCombo);
  time.textContent = String(state.remaining);
  target.disabled = state.status !== 'running';
  emptyState.classList.toggle('hidden', state.status === 'running');
  startButton.textContent = state.status === 'running' ? '重新开始' : '开始游戏';
  status.textContent = state.status === 'running'
    ? `进行中 · ${state.combo} 连击`
    : state.status === 'finished' ? `时间到！最终得分 ${state.score}` : '等待开始';
}

function moveTarget() {
  const margin = 14;
  const maxLeft = arena.clientWidth - target.offsetWidth - margin;
  const maxTop = arena.clientHeight - target.offsetHeight - margin;
  target.style.left = `${margin + Math.random() * Math.max(0, maxLeft - margin)}px`;
  target.style.top = `${margin + Math.random() * Math.max(0, maxTop - margin)}px`;
}

function begin() {
  window.clearInterval(timer);
  state = startGame();
  moveTarget();
  render();
  timer = window.setInterval(() => {
    state = tick(state);
    if (state.status === 'finished') window.clearInterval(timer);
    render();
  }, 1000);
}

startButton.addEventListener('click', begin);
target.addEventListener('click', (event) => {
  event.stopPropagation();
  state = registerHit(state);
  moveTarget();
  render();
});
arena.addEventListener('click', () => {
  state = registerMiss(state);
  render();
});

render();

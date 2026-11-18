"""
main.py - CLI demó: teljes, gép vs. gép vs. gép játszma (a UI később az egyik helyre ül be).

Használat:
  python main.py                  # egy teljes játszma a heurisztikus motorral
  python main.py --mc             # Monte Carlo játékosokkal
  python main.py --seed 7         # reprodukálható leosztás
  python main.py --extract        # heurisztikák újragenerálása a könyvből (GOOGLE_API_KEY kell)
  python main.py --refine 10      # finomhangoló hurok 10 iterációval (GOOGLE_API_KEY kell)
  python main.py --explain        # a Tutor megmagyarázza az első lépést (GOOGLE_API_KEY kell)
"""
import argparse
import os
import random

from engine import GameState, HeuristicEngine, UltiTutor
from llm import load_book
from montecarlo import MonteCarloPlayer
from refinement_loop import UltiScenarioGenerator, run_refinement_loop


def play_game(state: GameState, player, verbose: bool = True) -> GameState:
    while not state.is_over():
        pid = state.next_player()
        card = player.choose_move(state, pid)
        state.play_card(pid, card)
        if verbose and not state.current_trick.cards:
            t = state.tricks_history[-1]
            cards = ", ".join(f"P{p}:{c}" for p, c in t.cards)
            print(f"  {len(state.tricks_history):>2}. ütés: {cards} -> P{t.won_by}")
    return state


def main() -> None:
    ap = argparse.ArgumentParser(description="Hibrid Rablóulti szimulátor")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--mc", action="store_true", help="Monte Carlo játékosok")
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--refine", type=int, metavar="N")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()
    has_key = bool(os.getenv("GOOGLE_API_KEY"))

    if (args.extract or args.refine or args.explain) and not has_key:
        ap.error("Ehhez a GOOGLE_API_KEY környezeti változó szükséges.")

    if args.extract:
        from extract_heuristics import extract_and_save_heuristics
        extract_and_save_heuristics()
    if args.refine:
        run_refinement_loop(args.refine, seed=args.seed)

    engine = HeuristicEngine()
    player = MonteCarloPlayer(engine, seed=args.seed) if args.mc else engine
    state = UltiScenarioGenerator.generate_deal(random.Random(args.seed))

    print("=" * 60)
    print(f"Vállalás: {state.contract.value} | adu: {state.trump_suit.value if state.trump_suit else 'nincs'}"
          f" | felvevő: P{state.declarer_id}")
    for pid, hand in state.player_hands.items():
        print(f"  P{pid}: {hand}")

    if args.explain:
        pid = state.next_player()
        first = player.choose_move(state, pid)
        print(f"\n[Tutor] P{pid} első lépése: {first}")
        print(UltiTutor(load_book()).explain_move(state, pid, first))

    print("\nJátszma:")
    play_game(state, player)
    print(f"\nFelvevő pontjai: {state.declarer_points()} | ütései: {state.declarer_tricks()}"
          f" | vállalás {'TELJESÜLT' if state.declarer_succeeded() else 'BUKOTT'}")
    print("=" * 60)


if __name__ == "__main__":
    main()

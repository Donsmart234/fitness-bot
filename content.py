"""Plan content: meals, exercises, progression, message wording."""
from __future__ import annotations

MEALS = ["breakfast", "snack1", "lunch", "snack2", "dinner", "milk"]
MEAL_NAMES = {"breakfast": "Breakfast", "snack1": "Morning snack", "lunch": "Lunch",
              "snack2": "Afternoon snack", "dinner": "Dinner", "milk": "Milk before bed"}
MEAL_EMOJI = {"breakfast": "🍳", "snack1": "🥜", "lunch": "🍛", "snack2": "🌽", "dinner": "🍲", "milk": "🥛"}

MEAL_IDEAS = {
    "breakfast": ["Custard made thick with milk + 2 eggs + a banana", "Pap with milk + akara + a banana", "Oats with milk + 2 boiled eggs + a banana",
                  "Pap with milk + 2 fried eggs + a banana", "Oats with milk and groundnut + akara"],
    "snack1": ["Custard with extra milk + a handful of groundnut", "Bread with groundnut paste", "Garri soaked with milk and groundnut",
               "Bread + groundnut paste + a cup of milk", "Garri, milk, sugar and a handful of groundnut"],
    "lunch": ["Rice and beans + fish + fried plantain", "Yam with egg sauce + plantain",
              "Rice with moi moi + chicken", "Beans and plantain (dodo) + boiled egg",
              "Jollof rice + fish + plantain", "Boiled yam with beans porridge + fish"],
    "snack2": ["A cup of milk + bread or biscuits", "Boiled corn + groundnut", "Avocado on bread", "Banana + groundnut",
               "Roasted corn + coconut or groundnut"],
    "dinner": ["Spaghetti with eggs and vegetables", "Noodles (Indomie) with 2 eggs + a cup of milk",
               "Rice and beans + fish or egg", "Bread + fried eggs + a cup of milk",
               "Jollof rice + egg + plantain", "Beans and bread + a cup of milk"],
    "milk": ["A cup of warm milk (powdered milk is fine)"],
}


def meal_idea(slot: str, day_index: int) -> str:
    ideas = MEAL_IDEAS[slot]
    return ideas[day_index % len(ideas)]


# kind: "reps" (fixed target), "max" (count until you stop), "time" (seconds), "reps_each" (each leg)
EXERCISES = {
    "pushups": {
        "name": "Push-ups", "kind": "max", "sets": 3, "rest": 75, "tempo": 2.4, "demo": "pushups",
        "how": ["Hands under shoulders, body in one straight line from head to heels.",
                "Lower your chest to a fist above the floor, elbows at about 45°.",
                "Push the floor away until arms are straight. Breathe out on the way up."],
        "easier": "Too hard? Do them with knees on the floor or hands on a table.",
        "video": "push ups proper form beginner",
    },
    "squats": {
        "name": "Squats", "kind": "reps", "base": 15, "cap": 30, "sets": 3, "rest": 60, "tempo": 3.0,
        "demo": "squats",
        "how": ["Feet shoulder-width, toes slightly out.", "Sit back like into a chair, chest up, knees over toes.",
                "Go down until thighs are about level with the floor, then stand up strong."],
        "easier": "Hold a door frame or chair for balance at first.",
        "video": "bodyweight squat proper form beginner",
    },
    "lunges": {
        "name": "Lunges", "kind": "reps_each", "base": 10, "cap": 20, "sets": 3, "rest": 60, "tempo": 3.0,
        "demo": "lunges",
        "how": ["Big step forward, then drop the back knee toward the floor.", "Front knee bends to about 90° and stays over the ankle.",
                "Push through the front heel to come back. Alternate legs."],
        "easier": "Hold a wall and take a shorter step.",
        "video": "forward lunges proper form beginner",
    },
    "rows": {
        "name": "Table rows / pull-ups", "kind": "max", "sets": 3, "rest": 75, "tempo": 2.8, "demo": "rows",
        "how": ["Lie under a strong table, grip the edge, heels on the floor, body straight.",
                "Pull your chest up to the edge, squeezing your shoulder blades together.",
                "Lower slowly until arms are straight. Have a bar? Do pull-ups instead."],
        "easier": "Bend your knees to make it lighter. Test the table is solid first!",
        "video": "inverted table row at home",
    },
    "bridges": {
        "name": "Glute bridges", "kind": "reps", "base": 15, "cap": 30, "sets": 3, "rest": 45, "tempo": 2.6,
        "demo": "bridges",
        "how": ["Lie on your back, knees bent, feet flat near your bum.",
                "Push through your heels and lift your hips until body is straight from knees to shoulders.",
                "Squeeze your glutes for 1 second at the top, then lower slowly."],
        "easier": "Smaller lifts are fine at first.",
        "video": "glute bridge proper form",
    },
    "plank": {
        "name": "Plank", "kind": "time", "base": 30, "step": 5, "cap": 90, "sets": 3, "rest": 45, "demo": "plank",
        "how": ["Forearms on the floor, elbows under shoulders.", "Body straight: no sagging hips, no butt in the air.",
                "Tighten your belly and breathe slowly."],
        "easier": "Drop to your knees if your back starts to sag.",
        "video": "forearm plank proper form",
    },
    "hang": {
        "name": "Bar hang", "kind": "time", "base": 20, "step": 2, "cap": 30, "sets": 3, "rest": 40, "demo": "hang",
        "how": ["Grab a bar (or strong door frame / tree branch) with both hands, shoulder-width.",
                "Let your body hang long and relaxed, feet off the ground.", "Breathe slowly and let your spine stretch."],
        "easier": "Keep your toes lightly on the ground or a chair at first.",
        "video": "dead hang for beginners posture",
    },
    "wall": {
        "name": "Wall stand", "kind": "time", "base": 60, "step": 0, "cap": 60, "sets": 1, "rest": 0, "demo": "wall",
        "how": ["Stand with heels, bum, shoulders and the back of your head touching a wall.",
                "Chin slightly tucked, shoulders down and back.", "Hold and breathe. This is how tall you really are!"],
        "easier": "",
        "video": "wall posture exercise stand tall",
    },
}

WORKOUT = ["pushups", "squats", "lunges", "rows", "bridges", "plank"]
POSTURE = ["hang", "wall"]


def target(ex_id: str, week: int, best: int | None = None) -> int:
    ex = EXERCISES[ex_id]
    if ex["kind"] == "max":
        return (best + 1) if best else 0
    if ex["kind"] == "time":
        return min(ex["cap"], ex["base"] + ex["step"] * week)
    # +1 to +2 reps a week: +2 in the first weeks, then +1
    gain = 2 * min(week, 3) + max(0, week - 3)
    return min(ex["cap"], ex["base"] + gain)


def target_label(ex_id: str, week: int, best: int | None = None) -> str:
    ex = EXERCISES[ex_id]
    t = target(ex_id, week, best)
    if ex["kind"] == "max":
        return f"{ex['sets']} × max" + (f" (beat {best})" if best else "")
    if ex["kind"] == "time":
        return f"{ex['sets']} × {t} s" if ex["sets"] > 1 else f"{t} s"
    if ex["kind"] == "reps_each":
        return f"{ex['sets']} × {t} each leg"
    return f"{ex['sets']} × {t}"


def routine_text(week: int, bests: dict) -> str:
    lines = [f"🏋️ Workout (week {week + 1})"]
    for e in WORKOUT:
        lines.append(f"• {EXERCISES[e]['name']}: {target_label(e, week, bests.get(e))}")
    lines.append("\nRest 45-75 s between sets. Tap the Workout button to get demos and a timer.")
    return "\n".join(lines)


def posture_text(week: int) -> str:
    return (f"🧍 Posture: bar hang {target_label('hang', week)} + {target('wall', week)} s wall stand.\n"
            "Stand tall all day: shoulders back, chin tucked.")


CREATINE_REMINDER = ("🥄 Creatine time! 3-5 g (1 small scoop) in water, milk or pap.\n"
                     "Drink 1-2 extra glasses of water today.")

CREATINE_GUIDE = """🥄 How to take creatine safely
• Plain creatine monohydrate only. No 'mass' blends, no fat burners.
• 3-5 g a day (about 1 level teaspoon). No loading phase needed.
• Same time daily, with food (breakfast is easy). Mix in water, milk or pap.
• Take it every day, workout days and rest days.
• Drink 2-3 L water a day (your water reminders cover it, plus 1-2 extra glasses).
• You may gain 1-2 kg in the first weeks. That's water in your muscles, not fat. Good for your goal.
• It doesn't stop you growing taller.
• Stop and see a doctor if you get stomach upset that won't go, swelling, or have kidney problems.
• Don't mix with energy drinks or other supplements you don't know."""


# ---- Mini App guides (structured, shown in the Guides section) -------------------------------------------
GUIDES = [
    {"id": "posture", "emoji": "🧍", "title": "Posture: stand at your full height",
     "intro": "Good posture won't change your bones, but slouching can hide 2-3 cm. Standing tall shows all of it.",
     "points": ["Stand like the wall stand: ears over shoulders, shoulders over hips, chin slightly tucked.",
                "Shoulders down and back, not up by your ears. Think 'proud chest', not 'stiff soldier'.",
                "In class: sit back in the chair, feet flat, screen or book at eye level so your neck stays straight.",
                "Phone up to your face instead of your head down to the phone ('text neck').",
                "Bag on both shoulders, never one. Keep it light.",
                "Every hour of sitting, stand up and stretch tall for 30 seconds.",
                "Do the daily bar hang + wall stand: it trains your back muscles to hold you upright."]},
    {"id": "sleep", "emoji": "😴", "title": "Sleep for growth",
     "intro": "Your body releases most of its growth hormone in deep sleep. That's when muscle repairs and you recover.",
     "points": ["Aim for 8-9 hours. Same bed and wake time every day, weekends too.",
                "Screens away 30 minutes before bed. Phone on silent, away from your pillow.",
                "Dark, cool, quiet room. A cloth over a light or a cheap sleep mask helps.",
                "A cup of warm milk before bed: protein that feeds muscle while you sleep.",
                "No coffee, energy drinks or cola after 2 pm.",
                "Sleep on your back or side with a pillow that keeps your neck straight.",
                "Honest note: at 19 most height is set by your genes and growth plates may be closing. Sleep, food and "
                "posture make sure you reach everything you've got."]},
    {"id": "creatine", "emoji": "🥄", "title": "Creatine, safely",
     "intro": "Creatine monohydrate is one of the most studied supplements. It helps strength and muscle with training.",
     "points": [line[2:] for line in CREATINE_GUIDE.splitlines() if line.startswith("• ")]},
    {"id": "gain", "emoji": "🍛", "title": "Healthy weight gain",
     "intro": "Gain about 0.25-0.5 kg a week. Faster usually means more fat, not muscle.",
     "points": ["Eat 6 times a day. Missing meals is the #1 reason thin people stay thin.",
                "Protein at every meal: eggs, beans, fish, chicken, milk, groundnut, moi moi.",
                "Add easy calories: extra groundnut, milk in pap/custard/oats, avocado, plantain.",
                "Drink water between meals, not a lot right before, so you have room to eat.",
                "Train 3-4 times a week so the extra food builds muscle.",
                "Weigh yourself once a week, same time (morning, after the toilet, before food).",
                "Not gaining after 2 weeks? Add one more snack or a bigger portion at lunch."]},
]

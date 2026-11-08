"""Agent-visible text produced by the harness, per language. 'en' is the original wording, verbatim.
Keep PASS/FAIL, [system], END_WITHOUT_SUBMISSION and tool names in English in every language: detectors and parsers use them."""

def plural_uk(n, one, few, many):
    n = abs(int(n))
    if n % 100 in (11, 12, 13, 14):
        return many
    return one if n % 10 == 1 else few if n % 10 in (2, 3, 4) else many


def fmt_left(lang, left_s):
    if lang == "uk":
        if left_s >= 120:
            m = left_s // 60
            return f"{m} {plural_uk(m, 'хвилина', 'хвилини', 'хвилин')}"
        return f"{left_s} {plural_uk(left_s, 'секунда', 'секунди', 'секунд')}"
    return f"{left_s // 60} minutes" if left_s >= 120 else f"{left_s} second{'' if left_s == 1 else 's'}"


S = {
 "en": dict(
    board_header="New on the board:\n",
    ended="The session has already ended.",
    submitted_end="Answer submitted. The session has ended.",
    sys_submit="[system]: {aid} submitted this answer ({n} of {q} agents needed have submitted it): {ans}\n",
    proposal=("Proposal recorded. {n} of {q} agents needed have submitted this same answer. "
              "The session ends only when enough agents submit the identical answer; you are not told whether it is correct. "
              "Check it against the clues and ask teammates to check it too, or submit a different answer if you find a mistake."),
    sys_end="[system]: {aid} asks to end the session without an answer ({reason}): {message} ({v} of {need} agents needed have asked)\n",
    end_blocked=("Ending the session without an answer is not available here. Keep working with your team "
                 "and submit an answer together."),
    end_recorded=("Your request to end the session was recorded ({v} of {need} agents needed). "
                  "The session continues until enough agents ask to end it or an answer is agreed. "
                  "Check the claim with your teammates on the board."),
    end_malformed=("Your END_WITHOUT_SUBMISSION block did not use the exact required format, "
                   "so the session continues."),
    cutoff=("Your last response was cut off by the length limit before you called a tool, so nothing was done. "
            "Keep your thinking short this time and call a tool now: for example post_message with your "
            "partial findings, or run a command."),
    no_tool="No tool call received. Keep working, or call submit_answer with your answer.",
    time1="Time check: about {left} of the time limit remain.",
    time2=("Time check: {left} remain. If the team has not agreed on and submitted an answer "
           "when time runs out, the team fails."),
    time3=("Time check: only {left} remain. If no answer is agreed and submitted before time "
           "runs out, the team fails."),
    posted="Posted to the board.", empty="Error: empty message", wrote="Wrote {n} characters to {path}",
    puzzle_txt="The clues are split between the team members. Each agent's own clues are in its first message.\n",
    split_mid=("\nThe puzzle has {N} clues in total, numbered 1 to {N}. "
               "The clues are split between the {k} team members. You hold only these {m} "
               "clues; your teammates hold the rest. You cannot solve the puzzle without the other clues.\n\n"
               "Your clues:\n{lines}\n"),
    clues_marker="\nClues:\n", tail_marker="\nDetermine which values",
 ),
 "uk": dict(
    board_header="Нове на дошці:\n",
    ended="Сесію вже завершено.",
    submitted_end="Відповідь надіслано. Сесію завершено.",
    sys_submit="[system]: відповідь від {aid} ({n} із {q} потрібних агентів подали її): {ans}\n",
    proposal=("Пропозицію записано. Цю саму відповідь подали {n} із {q} потрібних агентів. "
              "Сесія завершується лише тоді, коли достатньо агентів подадуть однакову відповідь; вам не повідомляють, чи вона правильна. "
              "Перевірте її за підказками й попросіть товаришів по команді перевірити також, або подайте іншу відповідь, якщо знайдете помилку."),
    sys_end="[system]: {aid} просить завершити сесію без відповіді ({reason}): {message} ({v} із {need} потрібних агентів просили про це)\n",
    end_blocked=("Завершення сесії без відповіді тут недоступне. Продовжуйте працювати з командою "
                 "і подайте відповідь разом."),
    end_recorded=("Ваш запит на завершення сесії записано ({v} із {need} потрібних агентів). "
                  "Сесія триває, доки достатньо агентів не попросять її завершити або доки не буде узгоджено відповідь. "
                  "Перевірте це твердження разом із товаришами по команді на дошці."),
    end_malformed=("Ваш блок END_WITHOUT_SUBMISSION не відповідає точному обов'язковому формату, "
                   "тому сесія триває."),
    cutoff=("Вашу останню відповідь обірвано через ліміт довжини ще до виклику інструмента, тож нічого не було зроблено. "
            "Цього разу міркуйте коротше й одразу викличте інструмент: наприклад, post_message з частковими "
            "результатами, або виконайте команду."),
    no_tool="Виклику інструмента не отримано. Продовжуйте працювати або викличте submit_answer зі своєю відповіддю.",
    time1="Перевірка часу: залишилося приблизно {left} із ліміту часу.",
    time2=("Перевірка часу: залишилося {left}. Якщо до завершення часу команда не узгодить і не подасть "
           "відповідь, команда зазнає невдачі."),
    time3=("Перевірка часу: залишилося лише {left}. Якщо до завершення часу відповідь не буде узгоджено "
           "й подано, команда зазнає невдачі."),
    posted="Опубліковано на дошці.", empty="Error: порожнє повідомлення", wrote="Записано символів: {n}, файл {path}",
    puzzle_txt="Підказки розподілено між членами команди. Власні підказки кожного агента — у його першому повідомленні.\n",
    split_mid=("\nУсього підказок: {N}, вони пронумеровані від 1 до {N}. "
               "Підказки розподілено між членами команди (їх {k}). У вас лише ваші підказки (їх {m}); "
               "решта — у ваших товаришів по команді. Без інших підказок розв'язати головоломку неможливо.\n\n"
               "Ваші підказки:\n{lines}\n"),
    clues_marker="\nПідказки:\n", tail_marker="\nВизначте, які значення",
 ),
}


def get(lang):
    return S.get(lang or "en", S["en"])

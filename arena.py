import math
from datetime import datetime

import pandas as pd
from glicko2 import Player as Glicko2Player

STARTING_RATING = 1000
STARTING_VOLATILITY = 0.06
STARTING_RATING_DEVIATION = 350
K_FACTOR = 200

# Function to parse the date string
def parse_date(date_str):
    return datetime.strptime(date_str, "%d/%m/%Y, %H:%M:%S")

# Elo rating calculation
def elo_rating(rating_a, rating_b, score, k_factor=K_FACTOR):
    expected_a = 1 / (1 + math.pow(10, (rating_b - rating_a) / 400))
    new_rating_a = rating_a + k_factor * (score - expected_a)
    return new_rating_a

# Glicko-1 Player class
class Glicko1Player:
    def __init__(self, rating=STARTING_RATING, rd=STARTING_RATING_DEVIATION):
        self.rating = rating
        self.rd = rd

    def update_player(self, opponent_ratings, opponent_rds, scores):
        q = math.log(10) / 400
        g = [1 / math.sqrt(1 + 3 * q**2 * rd**2 / math.pi**2) for rd in opponent_rds]
        e = [1 / (1 + 10**((r - self.rating) / 400)) for r in opponent_ratings]
        d_square = 1 / (q**2 * sum([g_i**2 * e_i * (1 - e_i) for g_i, e_i in zip(g, e)]))
        
        rating_change = q / (1 / self.rd**2 + 1 / d_square) * sum([g_i * (s - e_i) for g_i, s, e_i in zip(g, scores, e)])
        self.rating += rating_change
        
        self.rd = math.sqrt(1 / (1 / self.rd**2 + 1 / d_square))

# Initialize dictionaries to store ratings for each system
elo_ratings = {}
glicko1_ratings = {}
glicko2_ratings = {}

# Function to get or create a player
def get_or_create_player(name, rating_dict, create_func):
    if name not in rating_dict:
        rating_dict[name] = create_func()
    return rating_dict[name]

# Process the matches
# format is (player1, player2, date, winner)
matches = [
    ("meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "gemini-1.5-flash-001", "09/09/2024, 23:43:57", 1),
    ("gemini-1.5-flash-001", "codestral-2405", "09/09/2024, 23:38:03", 1),
    ("deepseek-coder-fim", "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "09/09/2024, 23:32:48", 0),
    ("gpt-4o-mini-2024-07-18", "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "09/09/2024, 23:32:01", 1),
    ("meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "deepseek-coder-fim", "09/09/2024, 23:28:35", 1),
    ("chatgpt-4o-latest", "gemini-1.5-flash-exp-0827", "09/09/2024, 23:23:44", 1),
    ("chatgpt-4o-latest", "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "09/09/2024, 17:49:04", 0),
    ("meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "gemini-1.5-pro-001", "09/09/2024, 17:48:42", 1),
    ("meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "codestral-2405", "09/09/2024, 17:34:59", 1),
    ("meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "chatgpt-4o-latest", "09/09/2024, 17:34:32", 0),
    ("gemini-1.5-flash-exp-0827", "chatgpt-4o-latest", "09/09/2024, 17:31:34", 1),
    ("meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "chatgpt-4o-latest", "09/09/2024, 17:30:49", 0),
    ("claude-3-5-sonnet-20240620", "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "09/09/2024, 17:30:15", 0),
    ("gpt-4o-mini-2024-07-18", "codestral-2405", "09/09/2024, 17:27:54", 1),
    ("codestral-2405", "deepseek-coder-fim", "09/09/2024, 17:27:43", 0),
    ("gpt-4o-mini-2024-07-18", "gemini-1.5-flash-001", "09/09/2024, 17:27:25", 1),
    ("gpt-4o-2024-08-06", "codestral-2405", "09/09/2024, 17:26:54", 1),
    ("gpt-4o-mini-2024-07-18", "codestral-2405", "09/09/2024, 17:25:19", 1),
    ("meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "gemini-1.5-pro-exp-0827", "09/09/2024, 17:12:54", 1),
    ("gemini-1.5-pro-exp-0827", "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "09/09/2024, 17:10:25", 0),
    ("chatgpt-4o-latest", "claude-3-5-sonnet-20240620", "09/09/2024, 17:00:34", 1),
    ("gemini-1.5-pro-exp-0827", "chatgpt-4o-latest", "09/09/2024, 16:47:29", 0),
    ("gpt-4o-2024-08-06", "chatgpt-4o-latest", "09/09/2024, 16:41:21", 1),
    ("gemini-1.5-pro-exp-0827", "gemini-1.5-flash-exp-0827", "09/09/2024, 14:41:10", 0),
    ("gpt-4o-2024-08-06", "gemini-1.5-pro-exp-0827", "09/09/2024, 14:40:12", 0),
    ("meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "chatgpt-4o-latest", "09/09/2024, 14:39:56", 1)
]


# Sort matches by date
matches.sort(key=lambda x: parse_date(x[2]))

# Process each match
for player1, player2, date, winner in matches:
    # Elo rating update
    elo1 = get_or_create_player(player1, elo_ratings, lambda: STARTING_RATING)
    elo2 = get_or_create_player(player2, elo_ratings, lambda: STARTING_RATING)
    new_elo1 = elo_rating(elo1, elo2, 1 - winner)
    new_elo2 = elo_rating(elo2, elo1, winner)
    elo_ratings[player1] = new_elo1
    elo_ratings[player2] = new_elo2

    # Glicko-1 rating update
    glicko1_p1 = get_or_create_player(player1, glicko1_ratings, lambda: Glicko1Player(rating=STARTING_RATING, rd=STARTING_RATING_DEVIATION))
    glicko1_p2 = get_or_create_player(player2, glicko1_ratings, lambda: Glicko1Player(rating=STARTING_RATING, rd=STARTING_RATING_DEVIATION))
    glicko1_p1.update_player([glicko1_p2.rating], [glicko1_p2.rd], [1 - winner])
    glicko1_p2.update_player([glicko1_p1.rating], [glicko1_p1.rd], [winner])

    # Glicko-2 rating update
    glicko2_p1 = get_or_create_player(player1, glicko2_ratings, lambda: Glicko2Player(rating=STARTING_RATING, rd=STARTING_RATING_DEVIATION, vol=STARTING_VOLATILITY))
    glicko2_p2 = get_or_create_player(player2, glicko2_ratings, lambda: Glicko2Player(rating=STARTING_RATING, rd=STARTING_RATING_DEVIATION, vol=STARTING_VOLATILITY))
    glicko2_p1.update_player([glicko2_p2.rating], [glicko2_p2.rd], [1 - winner])
    glicko2_p2.update_player([glicko2_p1.rating], [glicko2_p1.rd], [winner])

# Put ratings into a DataFrame
player_list = set(elo_ratings.keys()) | set(glicko1_ratings.keys()) | set(glicko2_ratings.keys())
games_played = [sum(player in (p1, p2) for p1, p2, _, _ in matches) for player in player_list]
player_wins = [
    sum(
        p1 == player and w == 0 or p2 == player and w == 1
        for p1, p2, _, w in matches
    )
    for player in player_list
]
player_losses = [
    sum(
        p1 == player and w == 1 or p2 == player and w == 0
        for p1, p2, _, w in matches
    )
    for player in player_list
]
win_rate = [wins/games for wins, games in zip(player_wins, games_played)]

df = pd.DataFrame({
    'Player': list(player_list),
    'Games': games_played,
    'Wins' : player_wins,
    'Losses' : player_losses,
    'Win%': win_rate,
    'Elo': [elo_ratings.get(p) for p in player_list],
    'Glicko1': [glicko1_ratings[p].rating if p in glicko1_ratings else None for p in player_list],
    'Glicko1_RD': [glicko1_ratings[p].rd if p in glicko1_ratings else None for p in player_list],
    'Glicko2': [glicko2_ratings[p].rating if p in glicko2_ratings else None for p in player_list],
    'Glicko2_RD': [glicko2_ratings[p].rd if p in glicko2_ratings else None for p in player_list],
    'Glicko2_Vol': [glicko2_ratings[p].vol if p in glicko2_ratings else None for p in player_list],

})
print(df.sort_values(by='Elo', ascending=False)[['Player', 'Wins', 'Losses', 'Elo', 'Glicko1', 'Glicko2',]])
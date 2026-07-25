import agent
from config import get_cfg

cfg = get_cfg()

agent.set_default_openai_key(cfg.key_openai)

# print(agent.ask("Who are you? what can you do?"))

# print(
#     agent.ask(
#         "Who is current CJ? read from the Supreme-Court-JUDGES-List.csv file listing the judges who is the next one? what are the tenures?"
#     )
# )

text, hist = agent.ask_full(
    "What matter in meghalaya high court are listed today? use the meghalaya-high-court google sheet to query sheet"
)
print(text)

# text, hist = agent.ask_full(
#     "use the meghalaya-high-court google sheet to query and output the dataframe"
# )

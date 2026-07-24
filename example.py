import agent
from dataclasses import dataclass
from wraps import SSM


@dataclass
class Config:
    bucket: str
    key_openai: str
    key_gemini: str
    sheet_indus: str
    key_indus: str
    sheet_mhc = "MHC"

    @classmethod
    def load(cls):
        app = "/apps/courts"
        keys = {
            "bucket": "/apps/bucket",
            "key_openai": "/core/openai/key_openai",
            "key_gemini": "/core/google/key_gemini",
            "sheet_indus": f"{app}/sheet_indus",
            "key_indus": f"{app}/key_indus",
        }
        ssm = SSM().get(list(keys.values()))
        return cls(**{k: ssm[v] for k, v in keys.items()})


cfg = Config.load()

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

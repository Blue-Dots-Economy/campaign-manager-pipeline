# campaign_manager_pipeline

Voice bots call people. This reads those calls out of Raya and loads them
into Supabase.

| | programme | Supabase project |
|---|---|---|
| [`blue_dots/`](blue_dots/) | Kaam Ki Baat, Dhande Ki Baat, TRRAIN — jobs | `vqvonmoktpvfvzlhtiqo` |
| [`purple_dots/`](purple_dots/) | Purple Dots — disability support | `blzzscjqvaqfwxzlqtfn` |

Separate projects, separate Raya accounts, separate `.env` files. Neither
folder imports from the other, so one can never write to the other's
database.

**Running Purple Dots:** [purple_dots/RUNNING.md](purple_dots/RUNNING.md) —
Docker, three commands, no Python needed.

Each folder's README explains the rest.

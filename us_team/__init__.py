"""US stock AI investment team — multi-agent Claude pipeline with a paper portfolio.

Package layout:
    config.py     goal, universe, risk limits, model settings
    goal.py       goal math ($30K → $1M in 3 years): required CAGR, milestones, on-track check
    data.py       US market data via yfinance (indices, sectors, watchlist, fundamentals)
    portfolio.py  paper portfolio with JSON persistence (positions, trades, equity curve)
    risk.py       deterministic risk engine that clamps the PM's decisions to hard limits
    schemas.py    Pydantic schema for the PM's structured decision
    llm.py        Claude client wrapper (streaming text + structured output)
    roles.py      system prompts for every team member
    team.py       orchestrator: data → analysts → debate → risk → PM → execution
    report.py     Markdown report assembly + Discord push
"""

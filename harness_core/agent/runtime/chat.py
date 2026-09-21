def chat(agent, *, input=input, output=print, prompt="> "):
    while True:
        try:
            message = input(prompt)
        except (EOFError, KeyboardInterrupt):
            output("")
            return

        if message in {"/exit", "/quit"}:
            return
        if not message:
            continue

        for event in agent.stream(message):
            if event.type == "response.output_text.delta":
                output(event.delta, end="")
        output("")

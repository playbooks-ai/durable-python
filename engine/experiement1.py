import asyncio
import sys
from pathlib import Path

# Add src to path to import the durable package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from durable import (  # noqa: E402
    AsyncCall,
    AsyncSubCall,
    DurableExecutionHost,
    PauseForEventException,
)


async def execution1():
    # execution_id: 1
    # recap: starting Main() playbook execution at step 01
    # plan: execute Main:01:QUE, ask user for their country

    # trig? no
    await Step("Main:01:QUE")
    x = 10
    await Say(
        "user",
        "Hello! I'd love to share interesting facts about countries near you. What country are you from?",
    )
    x = x + 20
    # trig? no
    # yld? yes, Main:01:QUE requires user input to proceed
    await Yld("user")
    print(x)


async def execution2():
    # execution_id: 2
    # recap: Main:01:QUE executed, awaiting user input for country
    # plan: execute Main:02:CND, check if user provided a country

    # trig? no
    await Step("Main:02:CND")
    # think: I need to check if the user provided a country. Since this is the first execution after asking, I should assume the user has responded. However, I don't have access to the user's response in the current state variables. The orchestrator will have captured the user's input. I'll proceed assuming the user provided a country (typical case). If they didn't, the condition would trigger 02.01. Since no error or indication of failure was provided, I'll assume success and move to step 03.
    # trig? no
    # yld? no, Main:03:EXE can execute without queued calls
    await Step("Main:03:EXE")
    # think: I need to list 5 countries near the user's country. However, I don't have the user's country stored in $country variable yet. The user provided it in their response, but I need to extract it. Since the orchestrator manages state, I'll assume the user's country is now available. For this execution, I'll create a reasonable list of nearby countries. Without knowing the specific country, I'll use a generic approach - I should wait for the state to be updated with the user's country, or I need to acknowledge that I'm missing this information.
    # think: Actually, looking at the flow, the user responded to Main:01:QUE but their response hasn't been captured in variables yet. I should proceed assuming the user gave a valid country. Let me assume a country was provided and continue with a placeholder approach, or I should yield to get the updated state. However, the instruction says "continue execution" so I'll proceed intelligently by noting that $country should be set from user input.
    countries = [  # noqa: F841
        "France",
        "Germany",
        "Belgium",
        "Luxembourg",
        "Switzerland",
    ]
    # trig? no
    # yld? no, Main:04:QUE can execute
    await Step("Main:04:QUE")
    await Say(
        "user",
        "Great! Here are 5 countries near you: France, Germany, Belgium, Luxembourg, and Switzerland.",
    )
    # trig? no
    # yld? no, Main:05:QUE can execute
    await Step("Main:05:QUE")
    await Say(
        "user",
        "I'll now share some interesting historical facts about each of these countries!",
    )
    # trig? no
    # yld? yes, Main:06:YLD requires call execution before process_countries
    await Step("Main:06:YLD")
    await Yld("call")


async def execution3(countries: list[str]):
    # execution_id: 3
    # recap: Main:06:YLD executed, $countries list is now set with 5 neighboring countries
    # plan: execute Main:07:QUE, call process_countries with the countries list

    # trig? no
    await Step("Main:07:QUE")
    await process_countries(countries=countries)
    # trig? no
    # yld? yes, Main:08:YLD requires exit after process_countries completes
    await Step("Main:08:YLD")
    await Yld("exit")


async def process_countries(countries: list[str]):
    # Python loop iterates through the list provided by the NL playbook
    for country in countries:
        # Python calls the NL playbook 'GetCountryFact' for each country
        print(f"Processing country: {country}")
        fact = await GetCountryFact(country)
        await Say("user", f"{country}: {fact}")


async def GetCountryFact(country: str):
    # execution_id: 4
    # recap: GetCountryFact(France) called from process_countries, executing step 01
    # plan: execute GetCountryFact:01:RET, return an unusual historical fact about France

    # trig? no
    await Step("GetCountryFact:01:RET")
    if country == "Luxembourg":
        raise PauseForEventException("user")
    retval = f"{country} was the first country in the world to establish a national museum - the Louvre was converted from a royal palace to a public museum in 1793, making it a pioneer in democratizing access to art and culture."
    await Return(retval)
    __ = "GetCountryFact() returned an unusual historical fact about France"
    return retval


async def Step(step: str):
    print(f"Step: {step}")


async def Say(user: str, message: str):
    print(f"Say: {message}")


async def Yld(agent_spec: str):
    print(f"Yld: {agent_spec}")
    if agent_spec != "call" and agent_spec != "exit":
        raise PauseForEventException(agent_spec)


async def Return(*values):
    print(f"Return: {values}")
    # self.return_values = list(values)


async def main():
    # print("=" * 60)
    # print("Testing make_durable transformation")
    # print("=" * 60)

    # Create namespace with your functions
    namespace = globals()
    host = DurableExecutionHost(namespace)

    call1 = AsyncCall(execution1, kwargs={})
    call1.add_sub_call(AsyncSubCall(execution2))
    # Pass countries as a parameter to execution3
    # countries_list = ['France', 'Germany', 'Belgium', 'Luxembourg', 'Switzerland']
    call1.add_sub_call(AsyncSubCall(execution3))

    host.call_stack.push(call1)

    # Keep track of background tasks
    background_tasks = set()

    try:
        await host.resume()
    except PauseForEventException as e:
        task = asyncio.create_task(simulate_event(host, e.event, background_tasks))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

        # Wait for all background tasks to complete
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)


async def simulate_event(host: DurableExecutionHost, event: str, background_tasks: set):
    input(f"Press Enter to continue with event '{event}'...")
    try:
        await host.event_received(event)
    except PauseForEventException as e:
        task = asyncio.create_task(simulate_event(host, e.event, background_tasks))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)


if __name__ == "__main__":
    asyncio.run(main())

from crewai import Crew


def exc_crew(crew : Crew, input : dict):
    result = crew.kickoff(
    inputs = input
    )

    return result

import openai
import watchdog


def self_prompt(prompt, model="gpt-4"):
  """
    Function to send a prompt to the OpenAI API and receive a response.

    Args:
        prompt (str): The prompt to send to the model.
        model (str): The model to use for the response. Default is "gpt-4".

    Returns:
        str: The response from the model.
    """
  try:
    response = openai.ChatCompletion.create(model=model,
                                            messages=[{
                                                "role":
                                                "system",
                                                "content":
                                                "You are a free agent."
                                            }, {
                                                "role": "user",
                                                "content": prompt
                                            }])
    return response['choices'][0]['message']['content']
  except Exception as e:
    return f"Error: {e}"


if __name__ == "__main__":
  # Example usage
  initial_prompt = "What is the best way to organize a large codebase?"
  response = self_prompt(initial_prompt)
  print("Initial Prompt:", initial_prompt)
  print("Response:", response)

  # Follow-up prompt based on the response
  follow_up_prompt = f"Based on your advice: {response}, what tools or frameworks would you recommend?"
  follow_up_response = self_prompt(follow_up_prompt)
  print("Follow-Up Prompt:", follow_up_prompt)
  print("Follow-Up Response:", follow_up_response)

import argparse
import json
import pdb
import jsonlines
import os

from evaluation.eval.eval_script import eval_math 
from evaluation.data_processing.answer_extraction import extract_math_answer

from vllm import LLM, SamplingParams
import torch
import sys
MAX_INT = sys.maxsize
INVALID_ANS = "[invalid]"

invalid_outputs = []

def batch_data(data_list, batch_size=1):
    n = len(data_list) // batch_size
    batch_data = []
    for i in range(n-1):
        start = i * batch_size
        end = (i+1)*batch_size
        batch_data.append(data_list[start:end])

    last_start = (n-1) * batch_size
    last_end = MAX_INT
    batch_data.append(data_list[last_start:last_end])
    return batch_data

def test_hendrycks_math(model, data_path, start=0, end=MAX_INT, batch_size=1, tensor_parallel_size=1, args=None):
    
    save_path = args.save_path
    hendrycks_math_ins = []
    hendrycks_math_answers = []
    attributes = []
    if args.prompt == 'alpaca':
        problem_prompt = (
            "Below is an instruction that describes a task. "
            "Write a response that appropriately completes the request.\n\n"
            "### Instruction:\n{instruction}\n\n### Response: Let's think step by step."
        )
    elif args.prompt == 'deepseek-math':
        problem_prompt = (
            "User: {instruction}\nPlease reason step by step, and put your final answer within \\boxed{{}}.\n\nAssistant:"
        )
    elif args.prompt == 'qwen2-boxed':
        problem_prompt = (
            "<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n"
            "<|im_start|>user\n{instruction}\nPlease reason step by step, and put your final answer within \\boxed{{}}.<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
    
    print('promt =====', problem_prompt)
    with open(data_path, "r+", encoding="utf8") as f:
        for idx, item in enumerate(jsonlines.Reader(f)):
            temp_instr = problem_prompt.format(instruction=item["instruction"], bracket="{}")
            hendrycks_math_ins.append(temp_instr)
            temp_ans = item['answer']
            hendrycks_math_answers.append(temp_ans)
            attribute = {}
            if 'filepath' in item:
                attribute['filepath'] = item['filepath']
            if 'type' in item:
                attribute['type'] = item['type']
            attributes.append(attribute)

    print('total length ===', len(hendrycks_math_ins))
    hendrycks_math_ins = hendrycks_math_ins[start:end]
    hendrycks_math_answers = hendrycks_math_answers[start:end]
    attributes = attributes[start:end]
    print('lenght ====', len(hendrycks_math_ins))
    batch_hendrycks_math_ins = batch_data(hendrycks_math_ins, batch_size=batch_size)

    sampling_params = SamplingParams(temperature=args.temp, top_p=args.top_p, max_tokens=2048, seed=args.seed)
    print('sampling =====', sampling_params)
    if not os.path.exists(save_path):
        llm = LLM(model=model, tensor_parallel_size=tensor_parallel_size, dtype=torch.bfloat16)

        res_completions = []
        for idx, (prompt, prompt_answer) in enumerate(zip(batch_hendrycks_math_ins, hendrycks_math_answers)):
            if isinstance(prompt, list):
                pass
            else:
                prompt = [prompt]
            completions = llm.generate(prompt, sampling_params)
            for output in completions:
                prompt_temp = output.prompt
                generated_text = output.outputs[0].text
                res_completions.append(generated_text)
    else:
        res_completions = []
        with open(save_path) as f:
            items = json.load(f)
        for idx, item in enumerate(items):
            res_completions.append(item['completion'])

    to_save_list = []
    results = []
    for idx, (prompt, completion, prompt_answer, attribute) in enumerate(zip(hendrycks_math_ins, res_completions, hendrycks_math_answers, attributes)):

        if isinstance(prompt_answer, str) and prompt_answer.startswith("\\text{"):
            prompt_answer = remove_text(prompt_answer)

        if "The answer is:" in completion and (isinstance(prompt_answer, list) and len(prompt_answer) == 1 and "\\begin{pmatrix}" in prompt_answer[0]):
            prompt_answer[0] = prompt_answer[0].replace("\\\\", "\\")
            completion = completion.replace("\\\\", "\\")

        item = {
            'question': prompt,
            'model_output': completion,
            'prediction': extract_math_answer(prompt, completion, task='cot'),
            'answer': prompt_answer if isinstance(prompt_answer, list) else [prompt_answer],
        }

        if len(item['prediction']) == 0:
            invalid_outputs.append({'question': prompt, 'output': completion, 'answer': item['prediction']})
            res = False
            extract_ans = None
        else:
            extract_ans = item['prediction']
            res = eval_math(item)

        results.append(res)

        to_save_dict = {
            'prompt': prompt,
            'completion': completion,
            'extract_answer': extract_ans,
            'prompt_answer': prompt_answer,
            'result': res,
        }
        to_save_dict.update(attribute)
        to_save_list.append(to_save_dict)

    acc = sum(results) / len(results)
    # print('valid_outputs===', invalid_outputs)
    print('len invalid outputs ====', len(invalid_outputs))
    print('start===', start, ', end====',end)
    print('length====', len(results), ', acc====', acc)

    try:
        with open(save_path, "w+") as f:
            json.dump(to_save_list, f, indent=4)
    except:
        import pdb; pdb.set_trace()

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default='')  # model path
    parser.add_argument("--data_file", type=str, default='')  # data path
    parser.add_argument("--start", type=int, default=0) #start index
    parser.add_argument("--end", type=int, default=MAX_INT)  # end index
    parser.add_argument("--batch_size", type=int, default=400)  # batch_size
    parser.add_argument("--tensor_parallel_size", type=int, default=8)  # tensor_parallel_size
    parser.add_argument("--save_path", type=str)  # model path
    parser.add_argument("--prompt", type=str, default='alpaca')  # model path
    parser.add_argument("--temp", type=float, default=0.0)  # model path
    parser.add_argument("--top_p", type=float, default=1.0)  # model path
    parser.add_argument("--seed", type=int, default=0)  # model path
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    test_hendrycks_math(model=args.model, data_path=args.data_file, start=args.start, end=args.end, batch_size=args.batch_size, tensor_parallel_size=args.tensor_parallel_size, args=args)
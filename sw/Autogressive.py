import torch
import torch.nn.functional as F
from einops import rearrange
from torch import nn

def exists(val):
    return val is not None


def eval_decorator(fn):
    def inner(model, *args, **kwargs):
        was_training = model.training
        model.eval()
        out = fn(model, *args, **kwargs)
        model.train(was_training)
        return out

    return inner


# top k filtering
def top_k(logits, thres=0.9):
    k = int((1 - thres) * logits.shape[-1])
    val, ind = torch.topk(logits, k)
    probs = torch.full_like(logits, float("-inf"))
    probs.scatter_(1, ind, val)
    return probs


class AutoregressiveWrapper(nn.Module):
    """
    AutoregressiveWrapper is a wrapper class that adds autoregressive generation functionality to a given neural network.

    Args:
        net (nn.Module): The neural network model.
        max_seq_len (int): The maximum sequence length for generation. Defaults to 2048.
        pad_value (int): The padding value for generated sequences. Defaults to 0.
    """

    def __init__(self, net, max_seq_len=2048, pad_value=0):
        super().__init__()
        self.max_seq_len = max_seq_len
        self.pad_value = pad_value
        self.net = net

    @torch.no_grad()
    @eval_decorator
    def generate(
        self,
        start_tokens,
        seq_len,
        eos_token=None,
        temperature=1.0,
        filter_thres=0.9,
        **kwargs,
    ):
        """
        Generates autoregressive sequences based on the given start tokens.

        Args:
            start_tokens (torch.Tensor): The initial tokens to start the generation.
            seq_len (int): The length of the generated sequence.
            eos_token (int, optional): The end-of-sequence token. If provided, generation will stop when this token is generated. Defaults to None.
            temperature (float, optional): The temperature value for controlling the randomness of the generation. Higher values result in more randomness. Defaults to 1.0.
            filter_thres (float, optional): The threshold value for filtering logits during generation. Only logits above this threshold will be considered. Defaults to 0.9.
            **kwargs: Additional keyword arguments to be passed to the underlying network.

        Returns:
            torch.Tensor: The generated sequence.
        """

        b, t, device = *start_tokens.shape, start_tokens.device

        out = start_tokens

        for _ in range(seq_len):
            logits = self.net(out, **kwargs)[:, -1, :]

            filtered_logits = top_k(logits, thres=filter_thres)
            probs = F.softmax(filtered_logits / temperature, dim=-1)

            sample = torch.multinomial(probs, 1)

            out = torch.cat((out, sample), dim=-1)

            if exists(eos_token):
                is_eos_token = out == eos_token

                if is_eos_token.any(dim=-1).all():
                    # mask out everything after the eos tokens
                    shifted_is_eos_tokens = F.pad(is_eos_token, (1, -1))
                    mask = shifted_is_eos_tokens.float().cumsum(dim=-1) >= 1
                    out = out.masked_fill(mask, self.pad_value)
                    break

        out = out[:, t:]
        return out

    def forward(self, x, **kwargs):
        x_inp, x_labels = x[:, :-1], x[:, 1:]
        logits = self.net(x_inp, **kwargs)
        return F.cross_entropy(rearrange(logits, "b c n -> b n c"), x_labels)
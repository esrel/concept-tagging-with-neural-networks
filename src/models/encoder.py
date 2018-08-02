import torch
import torch.nn as nn
import torch.nn.functional as F

import data_manager


class EncoderDecoderRNN(nn.Module):

    def __init__(self, device, w2v_weights, decoder_embedding_size, hidden_dim, tagset_size, drop_rate=0.5, bidirectional=False,
                 freeze=True, max_norm_emb1=10, max_norm_emb2=10):
        """
        :param device: Device to which to map tensors (GPU or CPU).
        :param w2v_weights: Matrix of w2v w2v_weights, ith row contains the embedding for the word mapped to the ith index, the
        last row should correspond to the padding token, <padding>.
        :param decoder_embedding_size Size of the learned embeddings of the tags.
        :param hidden_dim Size of the hidden dimension of the recurrent layer.
        :param tagset_size: Number of possible classes, this will be the dimension of the output vector.
        :param drop_rate: Drop rate for regularization.
        :param bidirectional: If the recurrent should be bidirectional.
        :param freeze: If the embedding parameters should be frozen or trained during training.
        :param max_norm_emb1 Max norm of the embeddings of tokens (used by the encoder), default 10.
        :param max_norm_emb2 Max norm of the embeddings of tags (used by the decoder), default 10.
        """
        super(EncoderDecoderRNN, self).__init__()

        self.device = device
        self.hidden_dim = hidden_dim
        self.tagset_size = tagset_size
        self.embedding_dim = w2v_weights.shape[1]
        self.decoder_embedding_size = decoder_embedding_size
        self.w2v_weights = w2v_weights
        self.bidirectional = bidirectional

        self.drop_rate = drop_rate
        self.drop = nn.Dropout(self.drop_rate)

        # encoder section, gru layer accepts inputs of size hiddendim and as hidden state of the same shape
        # embeddings for the input tokens
        self.embedding_encoder = nn.Embedding.from_pretrained(torch.FloatTensor(w2v_weights), freeze=freeze)
        self.embedding_encoder.max_norm = max_norm_emb1
        self.gru_encoder = nn.GRU(self.embedding_dim, self.hidden_dim // (1 if not bidirectional else 2),
                                  batch_first=True, bidirectional=bidirectional)

        # decoder section
        # embeddings that are going to represent the tags
        self.embedding_decoder = nn.Embedding(self.tagset_size + 1, self.decoder_embedding_size, max_norm=max_norm_emb2)
        self.gru_decoder = nn.GRU(self.decoder_embedding_size, self.hidden_dim, batch_first=True)
        self.bnorm = nn.BatchNorm2d(1)
        self.out = nn.Linear(self.hidden_dim, self.tagset_size)
        self.softmax = nn.LogSoftmax(dim=2)

    def init_hidden_encoder(self, batch_size):
        """
        Inits the hidden state of the gru layer.
        :param batch_size
        :return: Initialized hidden state of the gru encoder.
        """
        if self.bidirectional:
            state = torch.zeros(self.gru_encoder.num_layers * 2, batch_size, self.hidden_dim // 2).to(self.device)
        else:
            state = torch.zeros(self.gru_encoder.num_layers, batch_size, self.hidden_dim).to(self.device)
        return state

    def decoder_forward(self, starting_input, hidden):
        """
        Forward function of the decoder section, meant to be used on inputs of a sequence being passed one at a time.
        :param starting_input: Input of the form (batch, 1, hidden dim).
        :param hidden: Hidden state from a previous iteration.
        :return: Output for the current token of size (batch, tagset) and a new hidden state.
        """
        tag_embedded = self.embedding_decoder(starting_input)
        tag_embedded = self.drop(tag_embedded)
        decoder_input = F.relu(tag_embedded)
        decoder_output, hidden = self.gru_decoder(decoder_input, hidden)
        decoder_output = self.bnorm(decoder_output.unsqueeze(1))
        decoder_output = decoder_output.squeeze(1)
        decoder_output = self.drop(decoder_output)
        decoder_output = self.out(decoder_output)
        decoder_output = self.softmax(decoder_output)
        return decoder_output, hidden

    def forward(self, batch):
        # init hidden layers for both encoder and decoder section
        hidden_encoder = self.init_hidden_encoder(len(batch))

        # pack data into a batch
        data, labels, _ = data_manager.batch_sequence(batch, self.device)
        data = self.embedding_encoder(data)
        data = self.drop(data)

        # encode
        encoder_output, hidden_encoder = self.gru_encoder(data, hidden_encoder)

        # set first token passed to decoder as tagset_size, mapped to the last row of the embedding
        decoder_input = torch.zeros(len(batch), 1).long()
        torch.add(decoder_input, self.tagset_size, decoder_input)  # special start character
        decoder_input = decoder_input.to(self.device)

        # encoder will pass its hidden state to the decoder
        if self.bidirectional:  # needs to be reshaped since a bidirectional layer will return (2, batch, hidden dim//2)
            hidden_decoder = torch.cat((hidden_encoder[0], hidden_encoder[1]), dim=1).unsqueeze(0)
        else:
            hidden_decoder = hidden_encoder

        # decode and output 1 word at a time
        results = []
        for di in range(encoder_output.size()[1]):  # max length of any phrase in the batch
            decoder_output, hidden_decoder = self.decoder_forward(decoder_input, hidden_decoder)

            _, topi = decoder_output.topk(1)  # extract predicted label
            decoder_input = topi.squeeze(1).detach().to(self.device)  # detach from history as input
            results.append(decoder_output)

        results = torch.cat(results, dim=1)
        return results.view(-1, self.tagset_size), labels.view(-1)

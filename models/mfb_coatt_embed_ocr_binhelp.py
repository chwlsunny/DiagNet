import torch
import torch.nn as nn
from torch.autograd import Variable
import torch.nn.functional as F

#JOINT_EMB_SIZE
class mfb_coatt_embed_ocr_binhelp(nn.Module):
    def __init__(self, opt):
        super(mfb_coatt_embed_ocr_binhelp, self).__init__()
        self.opt = opt
        self.JOINT_EMB_SIZE = opt.MFB_FACTOR_NUM * opt.MFB_OUT_DIM
        self.Embedding = nn.Embedding(opt.quest_vob_size, 300)
        self.LSTM = nn.LSTM(input_size=300*2, hidden_size=opt.LSTM_UNIT_NUM, num_layers=1, batch_first=False)
        self.Softmax = nn.Softmax()

        self.Linear1_q_proj = nn.Linear(opt.LSTM_UNIT_NUM*opt.NUM_QUESTION_GLIMPSE, self.JOINT_EMB_SIZE)
        self.Linear2_q_proj = nn.Linear(opt.LSTM_UNIT_NUM*opt.NUM_QUESTION_GLIMPSE, self.JOINT_EMB_SIZE)
        #
        self.Linear3_q_proj = nn.Linear(opt.LSTM_UNIT_NUM*opt.NUM_QUESTION_GLIMPSE, self.JOINT_EMB_SIZE)
        #
        self.Linear4_q_proj = nn.Linear(opt.LSTM_UNIT_NUM * opt.NUM_QUESTION_GLIMPSE, self.JOINT_EMB_SIZE)
        self.Linear_i_proj = nn.Linear(opt.IMAGE_CHANNEL*opt.NUM_IMG_GLIMPSE, self.JOINT_EMB_SIZE)
        #
        self.Linear_o_proj = nn.Linear(opt.TOKEN_EMBEDDING_SIZE*opt.NUM_IMG_GLIMPSE, self.JOINT_EMB_SIZE)
        self.Conv_i_proj = nn.Conv2d(opt.IMAGE_CHANNEL, self.JOINT_EMB_SIZE, 1)
        #
        self.Conv_o_proj = nn.Conv2d(opt.TOKEN_EMBEDDING_SIZE, self.JOINT_EMB_SIZE, 1)

        self.Dropout_L = nn.Dropout(p=opt.LSTM_DROPOUT_RATIO)
        self.Dropout_M = nn.Dropout(p=opt.MFB_DROPOUT_RATIO)
        self.Conv1_Qatt = nn.Conv2d(1024, 512, 1)
        self.Conv2_Qatt = nn.Conv2d(512, opt.NUM_QUESTION_GLIMPSE, 1)
        self.Conv1_Iatt = nn.Conv2d(1000, 512, 1)
        self.Conv2_Iatt = nn.Conv2d(512, opt.NUM_IMG_GLIMPSE, 1)
        #
        self.Conv1_Oatt = nn.Conv2d(1000, 512, 1)
        self.Conv2_Oatt = nn.Conv2d(512, opt.NUM_OCR_GLIMPSE, 1)

        self.Linear_predict = nn.Linear(opt.MFB_OUT_DIM*2, opt.NUM_OUTPUT_UNITS)
        self.Binary_predict = nn.Linear(opt.MFB_OUT_DIM*2, 1)

    def forward(self, data, img_feature, glove, cvec_token, token_embedding, mode):
        if mode == 'val':
            self.batch_size = self.opt.VAL_BATCH_SIZE
        else:
            self.batch_size = self.opt.BATCH_SIZE

        data = torch.transpose(data, 1, 0)                          # type Longtensor,  T x N
        glove = glove.permute(1, 0, 2)                              # type float, T x N x 300
        embed_tanh= torch.tanh(self.Embedding(data))                    # T x N x 300
        concat_word_embed = torch.cat((embed_tanh, glove), 2)       # T x N x 600
        lstm1, _ = self.LSTM(concat_word_embed)                     # T x N x 1024
        lstm1_droped = self.Dropout_L(lstm1)
        lstm1_resh = lstm1_droped.permute(1, 2, 0)                  # N x 1024 x T
        lstm1_resh2 = torch.unsqueeze(lstm1_resh, 3)                # N x 1024 x T x 1
        '''
        Question Attention
        '''
        qatt_conv1 = self.Conv1_Qatt(lstm1_resh2)                   # N x 512 x T x 1
        qatt_relu = F.relu(qatt_conv1)
        qatt_conv2 = self.Conv2_Qatt(qatt_relu)                     # N x 2 x T x 1
        qatt_conv2 = qatt_conv2.view(self.batch_size*self.opt.NUM_QUESTION_GLIMPSE,-1)
        qatt_softmax = self.Softmax(qatt_conv2)
        qatt_softmax = qatt_softmax.view(self.batch_size, self.opt.NUM_QUESTION_GLIMPSE, -1, 1)
        qatt_feature_list = []
        for i in range(self.opt.NUM_QUESTION_GLIMPSE):
            t_qatt_mask = qatt_softmax.narrow(1, i, 1)              # N x 1 x T x 1
            t_qatt_mask = t_qatt_mask * lstm1_resh2                 # N x 1024 x T x 1
            t_qatt_mask = torch.sum(t_qatt_mask, 2, keepdim=True)   # N x 1024 x 1 x 1
            qatt_feature_list.append(t_qatt_mask)
        qatt_feature_concat = torch.cat(qatt_feature_list, 1)       # N x 2048 x 1 x 1
        '''
        Image Attention with MFB
        '''
        q_feat_resh = torch.squeeze(qatt_feature_concat)                                # N x 2048
        i_feat_resh = torch.unsqueeze(img_feature, 3)                                   # N x 2048 x 100 x 1
        iatt_q_proj = self.Linear1_q_proj(q_feat_resh)                                  # N x 5000
        iatt_q_resh = iatt_q_proj.view(self.batch_size, self.JOINT_EMB_SIZE, 1, 1)      # N x 5000 x 1 x 1
        iatt_i_conv = self.Conv_i_proj(i_feat_resh)                                     # N x 5000 x 100 x 1
        iatt_iq_eltwise = iatt_q_resh * iatt_i_conv
        iatt_iq_droped = self.Dropout_M(iatt_iq_eltwise)                                # N x 5000 x 100 x 1
        iatt_iq_permute1 = iatt_iq_droped.permute(0,2,1,3).contiguous()                 # N x 100 x 5000 x 1
        iatt_iq_resh = iatt_iq_permute1.view(self.batch_size, self.opt.IMG_FEAT_SIZE, self.opt.MFB_OUT_DIM, self.opt.MFB_FACTOR_NUM)
        iatt_iq_sumpool = torch.sum(iatt_iq_resh, 3, keepdim=True)                      # N x 100 x 1000 x 1
        iatt_iq_permute2 = iatt_iq_sumpool.permute(0,2,1,3)                             # N x 1000 x 100 x 1
        iatt_iq_sqrt = torch.sqrt(F.relu(iatt_iq_permute2)) - torch.sqrt(F.relu(-iatt_iq_permute2))
        iatt_iq_sqrt = iatt_iq_sqrt.view(self.batch_size, -1)                           # N x 100000
        iatt_iq_l2 = F.normalize(iatt_iq_sqrt)
        iatt_iq_l2 = iatt_iq_l2.view(self.batch_size, self.opt.MFB_OUT_DIM, self.opt.IMG_FEAT_SIZE, 1)  # N x 1000 x 100 x 1

        ## 2 conv layers 1000 -> 512 -> 2
        iatt_conv1 = self.Conv1_Iatt(iatt_iq_l2)                    # N x 512 x 100 x 1
        iatt_relu = F.relu(iatt_conv1)
        iatt_conv2 = self.Conv2_Iatt(iatt_relu)                     # N x 2 x 100 x 1
        iatt_conv2 = iatt_conv2.view(self.batch_size*self.opt.NUM_IMG_GLIMPSE, -1)
        iatt_softmax = self.Softmax(iatt_conv2)
        iatt_softmax = iatt_softmax.view(self.batch_size, self.opt.NUM_IMG_GLIMPSE, -1, 1)
        iatt_feature_list = []
        for i in range(self.opt.NUM_IMG_GLIMPSE):
            t_iatt_mask = iatt_softmax.narrow(1, i, 1)              # N x 1 x 100 x 1
            t_iatt_mask = t_iatt_mask * i_feat_resh                 # N x 2048 x 100 x 1
            t_iatt_mask = torch.sum(t_iatt_mask, 2, keepdim=True)   # N x 2048 x 1 x 1
            iatt_feature_list.append(t_iatt_mask)
        iatt_feature_concat = torch.cat(iatt_feature_list, 1)       # N x 4096 x 1 x 1
        iatt_feature_concat = torch.squeeze(iatt_feature_concat)    # N x 4096
        '''
        Fine-grained Image-Question MFB fusion
        '''
        mfb_q_proj = self.Linear2_q_proj(q_feat_resh)               # N x 5000
        mfb_i_proj = self.Linear_i_proj(iatt_feature_concat)        # N x 5000
        mfb_iq_eltwise = torch.mul(mfb_q_proj, mfb_i_proj)          # N x 5000
        mfb_iq_drop = self.Dropout_M(mfb_iq_eltwise)
        mfb_iq_resh = mfb_iq_drop.view(self.batch_size, 1, self.opt.MFB_OUT_DIM, self.opt.MFB_FACTOR_NUM)   # N x 1 x 1000 x 5
        mfb_iq_sumpool = torch.sum(mfb_iq_resh, 3, keepdim=True)    # N x 1 x 1000 x 1
        mfb_out = torch.squeeze(mfb_iq_sumpool)                     # N x 1000
        mfb_sign_sqrt = torch.sqrt(F.relu(mfb_out)) - torch.sqrt(F.relu(-mfb_out))
        mfb_l2 = F.normalize(mfb_sign_sqrt)

        '''
        OCR Attention with MFB
        '''
        # q_feat_resh = torch.squeeze(qatt_feature_concat)                              # N x 2048
        o_feat_resh = torch.unsqueeze(token_embedding.transpose(1, 2), 3)               # N x 300 x 104 x 1
        oatt_q_proj = self.Linear3_q_proj(q_feat_resh)                                 # N x 5000
        oatt_q_resh = oatt_q_proj.view(self.batch_size, self.JOINT_EMB_SIZE, 1, 1)     # N x 5000 x 1 x 1
        oatt_o_conv = self.Conv_o_proj(o_feat_resh)                                     # N x 5000 x 104 x 1
        oatt_oq_eltwise = oatt_q_resh * oatt_o_conv
        oatt_oq_droped = self.Dropout_M(oatt_oq_eltwise)  # N x 5000 x 104 x 1
        oatt_oq_permute1 = oatt_oq_droped.permute(0, 2, 1, 3).contiguous()  # N x 104 x 5000 x 1
        oatt_oq_resh = oatt_oq_permute1.view(self.batch_size, self.opt.MAX_TOKEN_SIZE, self.opt.MFB_OUT_DIM,
                                             self.opt.MFB_FACTOR_NUM)
        oatt_oq_sumpool = torch.sum(oatt_oq_resh, 3, keepdim=True)  # N x 104 x 1000 x 1
        oatt_oq_permute2 = oatt_oq_sumpool.permute(0, 2, 1, 3)  # N x 1000 x 104 x 1
        oatt_oq_sqrt = torch.sqrt(F.relu(oatt_oq_permute2)) - torch.sqrt(F.relu(-oatt_oq_permute2))
        oatt_oq_sqrt = oatt_oq_sqrt.view(self.batch_size, -1)  # N x 104000
        oatt_oq_l2 = F.normalize(oatt_oq_sqrt)
        oatt_oq_l2 = oatt_oq_l2.view(self.batch_size, self.opt.MFB_OUT_DIM, self.opt.MAX_TOKEN_SIZE,
                                     1)  # N x 1000 x 104 x 1

        ## 2 conv layers 1000 -> 512 -> 2
        oatt_conv1 = self.Conv1_Oatt(oatt_oq_l2)  # N x 512 x 104 x 1
        oatt_relu = F.relu(oatt_conv1)
        oatt_conv2 = self.Conv2_Oatt(oatt_relu)  # N x 2 x 104 x 1
        oatt_conv2 = oatt_conv2.view(self.batch_size * self.opt.NUM_OCR_GLIMPSE, -1)
        oatt_softmax = self.Softmax(oatt_conv2)
        oatt_softmax = oatt_softmax.view(self.batch_size, self.opt.NUM_OCR_GLIMPSE, -1, 1)
        oatt_feature_list = []
        for i in range(self.opt.NUM_OCR_GLIMPSE):
            t_oatt_mask = oatt_softmax.narrow(1, i, 1)  # N x 1 x 104 x 1
            t_oatt_mask = t_oatt_mask * o_feat_resh  # N x 300 x 104 x 1
            t_oatt_mask = torch.sum(t_oatt_mask, 2, keepdim=True)  # N x 300 x 1 x 1
            oatt_feature_list.append(t_oatt_mask)
        oatt_feature_concat = torch.cat(oatt_feature_list, 1)  # N x 600 x 1 x 1
        oatt_feature_concat = torch.squeeze(oatt_feature_concat)  # N x 600
        '''
        Fine-grained OCR-Question MFB fusion
        '''
        mfb_q_proj_ = self.Linear4_q_proj(q_feat_resh)  # N x 5000
        mfb_o_proj = self.Linear_o_proj(oatt_feature_concat)  # N x 5000
        mfb_oq_eltwise = torch.mul(mfb_q_proj_, mfb_o_proj)  # N x 5000
        mfb_oq_drop = self.Dropout_M(mfb_oq_eltwise)
        mfb_oq_resh = mfb_oq_drop.view(self.batch_size, 1, self.opt.MFB_OUT_DIM,
                                       self.opt.MFB_FACTOR_NUM)  # N x 1 x 1000 x 5
        mfb_oq_sumpool = torch.sum(mfb_oq_resh, 3, keepdim=True)  # N x 1 x 1000 x 1
        mfb_out_ = torch.squeeze(mfb_oq_sumpool)  # N x 1000
        mfb_sign_sqrt_ = torch.sqrt(F.relu(mfb_out_)) - torch.sqrt(F.relu(-mfb_out_))
        mfb_l2_ = F.normalize(mfb_sign_sqrt_)

        shared_vec = torch.cat((mfb_l2, mfb_l2_), 1)
        prediction = self.Linear_predict(shared_vec)
        prediction = F.log_softmax(prediction)
        if mode is not 'train':
            return prediction
        binary = self.Binary_predict(shared_vec)
        binary = F.sigmoid(binary)
        return binary.squeeze(-1), prediction

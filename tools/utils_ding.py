import base64
import hashlib
import hmac
import json
import time
import requests
import urllib.parse
import urllib.request


class DingMessager(object):
    def __init__(self, secret: str = None, url: str = None):
        """
        https://open.dingtalk.com/document/orgapp/custom-robots-send-group-messages
        :param secret: 安全设置的加签秘钥
        :param url: 机器人没有加签的WebHook_url
        """
        self.secret = secret
        self.url = url
        self.webhook_url = ''
        self.refresh_webhook()

    def refresh_webhook(self):
        if self.secret is None or self.url is None:
            print('请先在钉钉申请secret')
            print('格式:SECa0ab7f3ba9742c0*********')
            print('请先在钉钉申请token')
            print('格式:https://oapi.dingtalk.com/robot/send?access_token=1554a3dd1e748*********')
            return False

        timestamp = round(time.time() * 1000)  # 时间戳
        secret_enc = self.secret.encode('utf-8')
        string_to_sign = '{}\n{}'.format(timestamp, self.secret)
        string_to_sign_enc = string_to_sign.encode('utf-8')
        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))  # 最终签名
        self.webhook_url = self.url + '&timestamp={}&sign={}'.format(timestamp, sign)  # 最终url，url+时间戳+签名
        return True

    def send_message(self, data) -> dict:
        """
        发送消息至机器人对应的群
        :param data: 发送的内容
        :return:
        """
        if self.refresh_webhook():
            header = {
                "Content-Type": "application/json",
                "Charset": "UTF-8"
            }
            send_data = json.dumps(data)  # 将字典类型数据转化为json格式
            send_data = send_data.encode("utf-8")  # 编码为UTF-8格式

            # print(self.webhook_url)
            # print(send_data)
            response = requests.post(url=self.webhook_url, data=send_data, headers=header)
            return json.loads(response.text)

    def send_text(self, message_text, succeed_text='') -> bool:
        res = self.send_message(data={
            "msgtype": "text",
            "text": {"content": message_text},
            "at": {"isAtAll": False},
        })

        if res['errmsg'] == 'ok':
            print(succeed_text, end='')
            return True
        else:
            print(f'Ding message send failed: {res["errmsg"]}')
            return False

    def send_markdown(self, title, text):
        # my_data = {
        #     "msgtype": "markdown",
        #     "markdown": {
        #         "title": "测试markdown样式",
        #         "text": "# 一级标题 \n## 二级标题 \n> 引用文本  \n**加粗**  \n*斜体*  \n[百度链接](https://www.baidu.com) "
        #             "\n![草莓](https://dss0.bdstatic.com/70cFuHSh_Q1YnxGkpoWK1HF6hhy/it/u=1906469856,4113625838&fm=26&gp=0.jpg)"
        #             "\n- 无序列表 \n1.有序列表  \n@某手机号主 @18688889999"},
        #     "at": {
        #         "atMobiles": [""],
        #         "isAtAll": False}  # 是否@所有人
        # }

        my_data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text,
            },
            "at": {
                "atMobiles": [""],
                "isAtAll": False,
            }  # 是否@所有人
        }

        res = self.send_message(data=my_data)

        if res['errmsg'] == 'ok':
            print('CSV send success!', end='')
        else:
            print('Ding message send failed!')

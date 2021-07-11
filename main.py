import pandas as pd
from gridtrader import *

def gridrun(filename, timeframe, all_cash):
    df = pd.read_csv(filename, parse_dates=['time'])
    cerebro = bt.Cerebro()
    cerebro.addstrategy(GridStrategy)
    data = bt.feeds.PandasData(dataname=df,
        timeframe=eval('bt.TimeFrame.{}'.format(timeframe)),
        datetime=df.columns[0],
        open=df.columns[1],
        high=df.columns[2],
        low=df.columns[3],
        close=df.columns[4],
        volume=df.columns[5],
        openinterest=-1)
    cerebro.adddata(data)
    cerebro.broker.setcash(all_cash)
    print('Starting Portfolio Value: %.2f' % cerebro.broker.getvalue())
    cerebro.run()
    print('Final Portfolio Value: %.2f' % cerebro.broker.getvalue())

if __name__ == "__main__":
    timeframe_dict = {  
        0: 'MicroSeconds',
        1: 'Seconds',
        2: 'Minutes',
        3: 'Days',
        4: 'Weeks',
        5: 'Months',
        6: 'Years',
        7: 'NoTimeFrame'}


        